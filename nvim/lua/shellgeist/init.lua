local M = {}

local rpc = require("shellgeist.rpc")
local sidebar = require("shellgeist.sidebar")
local diffview = require("shellgeist.diff")

local defaults = {
  socket = vim.fn.expand("~/.cache/shellgeist.sock"),
}

M._cfg = vim.deepcopy(defaults)

local function notify(msg, level)
  vim.schedule(function()
    vim.notify(msg, level or vim.log.levels.INFO, { title = "ShellGeist" })
  end)
end

local function trim(s)
  return (s:gsub("%s+$", ""))
end

local function git_root(cwd)
  local out = vim.fn.systemlist({ "git", "-C", cwd, "rev-parse", "--show-toplevel" })
  if vim.v.shell_error ~= 0 or not out or not out[1] or out[1] == "" then
    return nil
  end
  return trim(out[1])
end

local function project_root()
  local cwd = vim.loop.cwd()
  return git_root(cwd) or cwd
end

function M.setup(opts)
  M._cfg = vim.tbl_deep_extend("force", M._cfg, opts or {})
end

local function handle_result(ev, on_ok)
  if ev.type ~= "result" then
    return
  end
  if ev.ok then
    if on_ok then
      on_ok(ev.data or {}, ev)
    end
  else
    local err = ev.error or "error"
    local detail = ev.detail and (" (" .. ev.detail .. ")") or ""
    notify(err .. detail, vim.log.levels.ERROR)
  end
end

local function open_diff_fallback(diff_text, title)
  vim.cmd("tabnew")
  local buf = vim.api.nvim_get_current_buf()
  vim.api.nvim_buf_set_name(buf, title or "ShellGeist Diff")
  vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(diff_text or "", "\n", { plain = true }))
  vim.bo.buftype = "nofile"
  vim.bo.bufhidden = "wipe"
  vim.bo.swapfile = false
  vim.bo.filetype = "diff"
end

function M.reload_history(session_id, opts)
  session_id = session_id or "default"
  opts = opts or {}
  local on_done = opts.on_done
  local skip_last_user_if_content = opts.skip_last_user_if_content

  rpc.request(M._cfg.socket, { cmd = "get_history", session_id = session_id }, function(ev)
    if ev.ok and ev.data and ev.data.history then
      sidebar.render_welcome()
      local history = ev.data.history
      local skip_user_idx = nil
      if skip_last_user_if_content then
        for i = #history, 1, -1 do
          local msg = history[i]
          if msg.role == "user" and msg.content == skip_last_user_if_content then
            skip_user_idx = i
            break
          end
        end
      end

      for i, msg in ipairs(history) do
        if skip_user_idx and i == skip_user_idx then
          -- Skip
        else
          sidebar.append_text(msg.content, msg.role)
        end
      end
      if on_done then
        on_done()
      end
    else
      if on_done then
        on_done()
      end
    end
  end)
end

local function ensure_daemon(cb)
  local socket = M._cfg.socket
  
  -- Probe the socket instead of just checking file existence
  rpc.request(socket, { cmd = "ping" }, function(ev)
    if ev.status == "eof" then return end -- Ignore EOF message for non-streaming pings
    
    if ev.ok then
      cb()
    else
      -- If ping fails (likely ECONNREFUSED), try to spawn
      M._spawn_daemon(cb)
    end
  end)
end

function M._spawn_daemon(cb)
  local socket = M._cfg.socket
  
  -- We need the project root where the 'shellgeist' wrapper lives.
  local wrapper = "shellgeist"
  
  -- Fallback logic for finding the wrapper if not in PATH
  if vim.fn.executable(wrapper) == 0 then
    local plugin_path = debug.getinfo(1).source:sub(2):match("(.*/)")
    local project_path = vim.fn.fnamemodify(plugin_path .. "../../..", ":p:h")
    wrapper = project_path .. "/shellgeist"
  end

  if vim.fn.executable(wrapper) == 0 then
    notify("Error: ShellGeist wrapper not found.", vim.log.levels.ERROR)
    return
  end

  vim.fn.jobstart({ wrapper, "--daemon" }, {
    on_stderr = function(_, data)
      local debug_enabled = (vim.env.SHELLGEIST_DEBUG == "1")
      local lines = {}
      for _, ln in ipairs(data or {}) do
        local s = tostring(ln or "")
        if s ~= "" then
          if s:match("^DEBUG") then
            if debug_enabled then
              table.insert(lines, s)
            end
          else
            table.insert(lines, s)
          end
        end
      end
      local msg = table.concat(lines, "\n")
      if msg ~= "" then
        print("[ShellGeist Daemon Error] " .. msg)
      end
    end,
    on_exit = function(_, code)
      if code ~= 0 then notify("Daemon failed to start (exit code " .. code .. ")", vim.log.levels.ERROR) end
    end,
  })

  -- Wait for the daemon to respond to a ping (robust check)
  local attempts = 0
  local timer = vim.loop.new_timer()
  timer:start(500, 500, vim.schedule_wrap(function()
    attempts = attempts + 1
    
    rpc.request(socket, { cmd = "ping" }, function(ev)
      if timer:is_closing() then return end
      if ev.ok then
        timer:stop()
        timer:close()
        -- Do not reload_history here: when user is sending a goal we'd duplicate "User: goal"
        cb()
      elseif attempts > 20 then
        timer:stop()
        timer:close()
        notify("Timed out waiting for daemon to respond. Try running './shellgeist --daemon' manually.", vim.log.levels.ERROR)
      end
    end)
  end))
end

function M.run_agent(goal)
  local ok_run, run_err = pcall(function()
    goal = tostring(goal or "")
    if goal == "" then
      local ok_open, err_open = pcall(sidebar.open)
      if not ok_open then
        notify("sidebar.open failed: " .. tostring(err_open), vim.log.levels.ERROR)
        return
      end
      ensure_daemon(function()
        local session_id = vim.fn.sha256(project_root())
        M.reload_history(session_id)
      end)
      return
    end

    ensure_daemon(function()
      local root = project_root()
      local session_id = vim.fn.sha256(root)
      local was_open = sidebar.is_open()
      local ok_open, err_open = pcall(sidebar.open)
      if not ok_open then
        notify("sidebar.open failed: " .. tostring(err_open), vim.log.levels.ERROR)
        return
      end

      local function run_agent()
        local prefers_v5_events = false

        local function handle_execution_event(ev)
          if ev.type ~= "execution_event" or type(ev.event) ~= "table" then
            return false
          end

          prefers_v5_events = true
          local event = ev.event
          local channel = tostring(event.channel or "")
          local content = tostring(event.content or "")
          local phase = tostring(event.phase or "")
          local meta = type(event.meta) == "table" and event.meta or {}

          if channel == "status" then
            local thinking = false
            if type(meta.thinking) == "boolean" then
              thinking = meta.thinking
            elseif phase == "thinking" or phase == "streaming" or phase == "tool_use" then
              thinking = true
            end
            sidebar.set_thinking(thinking)
            return true
          end

          if channel == "reasoning" then
            sidebar.append_text(content, "thinking", meta)
          elseif channel == "response" then
            if meta.chunk then
              sidebar.append_text(content, "response_chunk", meta)
            else
              sidebar.append_text(content, "response", meta)
            end
          elseif channel == "tool_call" then
            sidebar.append_text(content, "action", meta)
          elseif channel == "code" then
            sidebar.append_text(content, "code", meta)
          elseif channel == "tool_result" then
            sidebar.append_text(content, "observation", meta)
          elseif channel == "error" then
            sidebar.append_text(content, "error", meta)
          end
          return true
        end

        sidebar.append_text(goal, "user")
        rpc.request(M._cfg.socket, { cmd = "agent_task", root = root, goal = goal, session_id = session_id }, function(ev)
          if ev.status == "eof" then
            return
          end
          if handle_execution_event(ev) then
            return
          end
          if prefers_v5_events and (ev.type == "log" or ev.type == "status") then
            return
          end
          if ev.type == "log" then
            sidebar.append_text(ev.content, ev.log_type)
            return
          end
          if ev.type == "status" then
            sidebar.set_thinking(ev.thinking)
            return
          end
          if ev.ok then
            -- Final block handling
          else
            handle_result(ev)
          end
        end, { stream = true })
      end

      if was_open then
        run_agent()
      else
        M.reload_history(session_id, {
          skip_last_user_if_content = goal,
          on_done = run_agent
        })
      end
    end)
  end)
  if not ok_run then
    notify("run_agent failed: " .. tostring(run_err), vim.log.levels.ERROR)
  end
end

-- Commands
vim.api.nvim_create_user_command("SGSidebar", function()
  sidebar.toggle()
end, {})

vim.api.nvim_create_user_command("SGAgent", function(opts)
  M.run_agent(opts.args)
end, { nargs = "*" })

vim.api.nvim_create_user_command("SGPing", function()
  ensure_daemon(function()
    rpc.request(M._cfg.socket, { cmd = "ping" }, function(ev)
      handle_result(ev, function()
        notify("ok")
      end)
    end)
  end)
end, {})

vim.api.nvim_create_user_command("SGChat", function(opts)
  local text = opts.args
  rpc.request(M._cfg.socket, { cmd = "chat", text = text }, function(ev)
    handle_result(ev, function(res)
      if res.answer then
        notify(res.answer)
      else
        notify("ok")
      end
    end)
  end)
end, { nargs = "+" })

vim.api.nvim_create_user_command("SGPlan", function(opts)
  local goal = opts.args
  local root = project_root()
  rpc.request(M._cfg.socket, { cmd = "plan", root = root, goal = goal }, function(ev)
    handle_result(ev, function(res)
      notify("plan ok (" .. tostring(#(res.steps or {})) .. " steps)")
    end)
  end)
end, { nargs = "+" })

vim.api.nvim_create_user_command("SGShell", function(opts)
  local task = opts.args
  local root = project_root()
  rpc.request(M._cfg.socket, { cmd = "shell", root = root, task = task }, function(ev)
    handle_result(ev, function(res)
      local blocked = res.blocked or {}
      if #blocked > 0 then
        notify("shell plan: blocked " .. tostring(#blocked) .. " cmd(s)", vim.log.levels.WARN)
      else
        notify("shell plan ok (" .. tostring(#(res.commands or {})) .. " cmd(s))")
      end
    end)
  end)
end, { nargs = "+" })

vim.api.nvim_create_user_command("SGEdit", function(opts)
  -- SGEdit <file> <instruction...>
  local fargs = opts.fargs
  if #fargs < 2 then
    notify("usage: :SGEdit <file> <instruction...>", vim.log.levels.ERROR)
    return
  end

  local file = fargs[1]
  local instruction = table.concat(fargs, " ", 2)
  local root = project_root()

  -- immediate feedback (so never "silent")
  notify("edit: sending request...")

  rpc.request(M._cfg.socket, { cmd = "edit", root = root, file = file, instruction = instruction }, function(ev)
    handle_result(ev, function(res)
      if res.diff and type(res.diff) == "string" and res.diff ~= "" then
        local ok, err = pcall(function()
          diffview.preview(res.diff, {
            root = root,
            file = file,
            patch = res.patch,
            full_replace = res.full_replace,
            instruction = instruction,
          })
        end)

        if ok then
          notify("edit ok (diff opened)")
        else
          notify("diff preview failed: " .. tostring(err), vim.log.levels.ERROR)
          open_diff_fallback(res.diff, "ShellGeist Diff (fallback)")
        end
      else
        notify("edit ok (no diff)")
      end
    end)
  end)
end, { nargs = "+", complete = "file" })

vim.api.nvim_create_user_command("SGStatus", function()
  local root = project_root()
  rpc.request(M._cfg.socket, { cmd = "git_status", root = root }, function(ev)
    handle_result(ev, function(res)
      if res.inside_git == false then
        notify("not a git repo", vim.log.levels.WARN)
        return
      end

      local lines = res.porcelain or {}
      if #lines == 0 then
        notify("git clean")
        return
      end

      notify("git status: " .. tostring(#lines) .. " change(s)")
      vim.cmd("tabnew")
      vim.api.nvim_buf_set_lines(0, 0, -1, false, lines)
      vim.bo.filetype = "git"
      vim.bo.buftype = "nofile"
      vim.bo.bufhidden = "wipe"
      vim.bo.swapfile = false
    end)
  end)
end, {})

return M
