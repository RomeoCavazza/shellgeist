local M = {}

local rpc = require("shellgeist.rpc")
local sidebar = require("shellgeist.sidebar")
local diffview = require("shellgeist.diff")
local conflict = require("shellgeist.conflict")

local defaults = {
  socket = vim.fn.expand("~/.cache/shellgeist.sock"),
  -- Phase 1 refactor: when false, sidebar is not reloaded at end of run (no full wipe; timeline stays as shown)
  reload_sidebar_on_run_done = false,
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

local function is_unusable_root(path)
  path = tostring(path or "")
  if path == "" then
    return true
  end
  local norm = vim.fn.fnamemodify(path, ":p")
  local home = vim.fn.expand("~")
  return norm == "/" or norm == home .. "/"
end

local function pick_safe_root(...)
  for i = 1, select("#", ...) do
    local candidate = select(i, ...)
    if candidate and candidate ~= "" and not is_unusable_root(candidate) then
      return candidate
    end
  end
  for i = 1, select("#", ...) do
    local candidate = select(i, ...)
    if candidate and candidate ~= "" then
      return candidate
    end
  end
  return nil
end

local function project_path_from_plugin()
  local plugin_path = debug.getinfo(1).source:sub(2):match("(.*/)") or ""
  return vim.fn.fnamemodify(plugin_path .. "../../..", ":p:h")
end

local function git_root(cwd)
  local out = vim.fn.systemlist({ "git", "-C", cwd, "rev-parse", "--show-toplevel" })
  if vim.v.shell_error ~= 0 or not out or not out[1] or out[1] == "" then
    return nil
  end
  return trim(out[1])
end

local function current_buffer_root()
  local ok, buf = pcall(vim.api.nvim_get_current_buf)
  if not ok or not buf then
    return nil
  end
  local name = vim.api.nvim_buf_get_name(buf)
  if not name or name == "" then
    return nil
  end
  local abs = vim.fn.fnamemodify(name, ":p")
  if abs == "" then
    return nil
  end
  local dir = vim.fn.fnamemodify(abs, ":h")
  if not dir or dir == "" then
    return nil
  end
  return git_root(dir) or dir
end

local function root_from_goal(goal)
  goal = tostring(goal or "")
  if goal == "" then
    return nil
  end

  local function nearest_existing_dir(path_abs)
    local cur = vim.fn.fnamemodify(path_abs, ":p")
    if cur == "" then
      return nil
    end
    while cur and cur ~= "" do
      local st = vim.loop.fs_stat(cur)
      if st and st.type == "directory" then
        if is_unusable_root(cur) then
          return nil
        end
        return cur
      end
      local parent = vim.fn.fnamemodify(cur, ":h")
      if not parent or parent == "" or parent == cur then
        break
      end
      cur = parent
    end
    return nil
  end

  -- Extract absolute path-like tokens from user prompt and try to infer
  -- a project root from them (file path -> parent dir -> git root).
  for token in goal:gmatch("(/[^%s\"'`()%[%]{}<>|;:,]+)") do
    local p = vim.fn.fnamemodify(token, ":p")
    if p and p ~= "" then
      local st = vim.loop.fs_stat(p)
      local dir = nil
      if st and st.type == "file" then
        dir = vim.fn.fnamemodify(p, ":h")
      elseif st and st.type == "directory" then
        dir = p
      elseif token:match("%.[A-Za-z0-9_]+$") then
        -- Deterministic file-target inference even when file doesn't exist yet.
        dir = nearest_existing_dir(vim.fn.fnamemodify(p, ":h"))
      end
      if dir and dir ~= "" then
        return pick_safe_root(git_root(dir), dir)
      end
    end
  end

  -- Fallback: extract absolute file path even if followed by punctuation/markdown.
  local raw_abs = goal:match("(/[^%s\"'`]+%.[A-Za-z0-9_]+)")
  if raw_abs and raw_abs ~= "" then
    local cleaned = raw_abs:gsub("[,.;:]+$", "")
    local p = vim.fn.fnamemodify(cleaned, ":p")
    if p and p ~= "" then
      local dir = nearest_existing_dir(vim.fn.fnamemodify(p, ":h"))
      if dir and dir ~= "" then
        return pick_safe_root(git_root(dir), dir)
      end
    end
  end

  return nil
end

local function project_root()
  -- Prefer the active buffer location: users often open Neovim from $HOME
  -- while editing a file inside a project repository.
  local from_buffer = current_buffer_root()
  if from_buffer and from_buffer ~= "" and not is_unusable_root(from_buffer) then
    return from_buffer
  end

  local cwd = vim.loop.cwd() or vim.fn.getcwd()
  if cwd and cwd ~= "" then
    local from_cwd = git_root(cwd) or cwd
    local home = vim.fn.expand("~")
    if is_unusable_root(from_cwd) and M._last_root and M._last_root ~= "" and not is_unusable_root(M._last_root) then
      return M._last_root
    end
    if from_cwd == home and M._last_root and M._last_root ~= "" and M._last_root ~= home then
      return M._last_root
    end
    if not is_unusable_root(from_cwd) then
      return from_cwd
    end
  end

  -- Last-resort fallback: plugin project path is safer than $HOME.
  local from_plugin = project_path_from_plugin()
  if from_plugin and from_plugin ~= "" and not is_unusable_root(from_plugin) then
    return from_plugin
  end
  return pick_safe_root(M._last_root, from_plugin, from_buffer, cwd, vim.fn.expand("~"))
end

M._mode = "auto"  -- "auto" or "review"

M._last_root = nil
M._last_session_id = nil
M._conversation_fresh = false  -- set true only by explicit "new conversation" action

function M.setup(opts)
  M._cfg = vim.tbl_deep_extend("force", M._cfg, opts or {})
end

function M.set_mode(mode)
  M._mode = mode or "auto"
end

function M.get_mode()
  return M._mode or "auto"
end

function M.get_last_context()
  return {
    root = M._last_root,
    session_id = M._last_session_id,
    mode = M.get_mode(),
  }
end

--- Update last root/session from current project (e.g. when sidebar is opened without running agent).
function M.set_context_from_project()
  local root = project_root()
  M._last_root = root
  M._last_session_id = root and vim.fn.sha256(root) or nil
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
        if skip_user_idx and i >= skip_user_idx then
          -- Skip this message and all subsequent (will be re-generated by run_agent)
        else
          -- When loading from DB, user messages containing <tool_observation> are tool results: render as timeline + observation card
          local content = type(msg.content) == "string" and msg.content or ""
          if msg.role == "user" and content:find("<tool_observation") then
            local name = content:match('<tool_observation name="([^"]+)"')
            local after_gt = content:find(">", 1, true)
            local end_tag = "\n</tool_observation>"
            local end_pos = content:find(end_tag, 1, true)
            local inner = nil
            if after_gt and end_pos and end_pos > after_gt then
              inner = content:sub(after_gt + 2, end_pos - 1)
            end
            if name and inner then
              sidebar.append_text(inner, "history_observation", { tool = name })
            else
              sidebar.append_text(msg.content, msg.role)
            end
          else
            sidebar.append_text(msg.content, msg.role)
          end
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
  local expected_project = project_path_from_plugin()
  local function norm_path(p)
    p = tostring(p or "")
    if p == "" then return "" end
    return vim.fn.fnamemodify(p, ":p:h")
  end
  
  -- Probe the socket instead of just checking file existence
  local called = false
  rpc.request(socket, { cmd = "ping" }, function(ev)
    if ev.status == "eof" then return end -- Ignore EOF message for non-streaming pings
    if called then return end -- Guard against double callback
    called = true

    if ev.ok then
      local daemon_root = nil
      if type(ev.data) == "table" then
        daemon_root = ev.data.repo_root
      end
      if norm_path(daemon_root) ~= "" and norm_path(daemon_root) == norm_path(expected_project) then
        cb()
      else
        -- Connected daemon is stale or from another checkout; respawn local one.
        M._spawn_daemon(cb)
      end
    else
      -- If ping fails (likely ECONNREFUSED), try to spawn
      M._spawn_daemon(cb)
    end
  end)
end

function M._spawn_daemon(cb)
  local socket = M._cfg.socket
  
  -- Resolve the wrapper from the plugin's own location (project root).
  -- IMPORTANT: Do NOT use PATH first — stale copies (e.g. ~/.local/bin/shellgeist)
  -- can resolve there and fail because $DIR/backend won't exist.
  local project_path = project_path_from_plugin()
  local wrapper_path = project_path .. "/shellgeist"
  local cmd = nil

  -- Prefer project wrapper even if not executable: run via bash to avoid PATH stale binary.
  if vim.fn.filereadable(wrapper_path) == 1 then
    cmd = { "bash", wrapper_path, "daemon" }
  elseif vim.fn.executable("shellgeist") == 1 then
    cmd = { "shellgeist", "daemon" }
  end

  if not cmd then
    notify("Error: ShellGeist wrapper not found.", vim.log.levels.ERROR)
    return
  end

  vim.fn.jobstart(cmd, {
    on_stderr = function(_, data)
      local debug_enabled = (vim.env.SHELLGEIST_DEBUG == "1")
      local lines = {}
      for _, ln in ipairs(data or {}) do
        local s = tostring(ln or "")
        if s ~= "" then
          -- Filter out common Nix informational messages that aren't errors
          local is_nix_info = s:find("^building") 
                           or s:find("^fetching")
                           or s:find("^copying")
                           or s:find("^evaluating")
                           or s:find("^instantiating")
                           or s:find("^derivation")

          if not is_nix_info then
            if s:match("^DEBUG") then
              if debug_enabled then
                table.insert(lines, s)
              end
            else
              table.insert(lines, s)
            end
          end
        end
      end
      local msg = table.concat(lines, "\n")
      if msg ~= "" then
        print("[ShellGeist] " .. msg)
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
        local root = project_root()
        local session_id = vim.fn.sha256(root)
        M._last_root = root
        M._last_session_id = session_id
        sidebar.focus_prompt()
      end)
      return
    end

    ensure_daemon(function()
      local root = pick_safe_root(
        root_from_goal(goal),
        current_buffer_root(),
        project_path_from_plugin(),
        project_root(),
        M._last_root
      )
      local session_id = vim.fn.sha256(root)
      M._last_root = root
      M._last_session_id = session_id
      local was_open = sidebar.is_open()
      local ok_open, err_open = pcall(sidebar.open)
      if not ok_open then
        notify("sidebar.open failed: " .. tostring(err_open), vim.log.levels.ERROR)
        return
      end

      local function run_agent()
        local current_reply_fn = nil

        local function handle_event(ev, reply)
          if reply then current_reply_fn = reply end

          if ev.type ~= "execution_event" or type(ev.event) ~= "table" then
            return false
          end

          local event = ev.event
          local channel = tostring(event.channel or "")
          local content = tostring(event.content or "")
          local phase = tostring(event.phase or "")
          local meta = type(event.meta) == "table" and event.meta or {}
          if not meta.root or meta.root == "" then
            meta.root = root
          end
          if not meta.mode or meta.mode == "" then
            meta.mode = agent_mode
          end

          if channel == "status" then
            local thinking = false
            if type(meta.thinking) == "boolean" then
              thinking = meta.thinking
            elseif phase == "thinking" or phase == "streaming" or phase == "tool_use" then
              thinking = true
            end
            -- Show rich status text (e.g. "[read] main.py") when available
            if thinking and content ~= "" then
              sidebar.set_thinking(true)
              if sidebar.prompt and sidebar.prompt.border then
                pcall(function() sidebar.prompt.border:set_text("top", " " .. content:sub(1, 40) .. " ", "center") end)
              end
            else
              sidebar.set_thinking(thinking)
            end
            return true
          end

          -- ── File changed: show diff in sidebar ──
          if channel == "file_changed" then
            local file_rel = meta.file or content or ""
            local file_root = meta.root or root
            if file_rel ~= "" then
              vim.schedule(function()
                local filepath = file_rel
                if not filepath:match("^/") then
                  filepath = file_root .. "/" .. filepath
                end
                -- Get old content from git
                local old_cmd = { "git", "-C", file_root, "show", "HEAD:" .. file_rel }
                local old_out = vim.fn.systemlist(old_cmd)
                if vim.v.shell_error ~= 0 then old_out = {} end
                -- Get current file content
                local current_lines = {}
                if vim.fn.filereadable(filepath) == 1 then
                  current_lines = vim.fn.readfile(filepath)
                end
                -- Compute unified diff and show as code card in sidebar
                if #old_out > 0 or #current_lines > 0 then
                  local old_text = (#old_out > 0) and (table.concat(old_out, "\n") .. "\n") or ""
                  local new_text = (#current_lines > 0) and (table.concat(current_lines, "\n") .. "\n") or ""
                  local ok_d, diff_text = pcall(vim.diff, old_text, new_text, {
                    algorithm = "histogram",
                    result_type = "unified",
                    ctxlen = 2,
                  })
                  if ok_d and diff_text and diff_text ~= "" then
                    sidebar.append_text(diff_text, "code", { file = file_rel })
                  else
                    sidebar.append_text("Changed: " .. file_rel, "observation", { file = file_rel })
                  end
                end
              end)
            end
            return true
          end

          -- ── Review mode: hunk-level review for edit_file ──
          if channel == "review_pending" then
            local file_rel = meta.file or content or ""
            local old_content = meta.old_content or ""
            local new_content = meta.new_content or ""

            if file_rel ~= "" then
              sidebar.set_thinking(false)
              sidebar.append_text("", "diff_review", {
                file = file_rel,
                root = root,
                old_content = old_content,
                new_content = new_content,
                reply_fn = current_reply_fn,
              })
            end
            return true
          end

          -- ── Review mode: approval request ──
          if channel == "approval_request" then
            local tool = meta.tool or content or "unknown"
            local args = meta.args or {}
            local args_str = vim.json.encode(args)
            if #args_str > 300 then args_str = args_str:sub(1, 300) .. "..." end

            sidebar.set_thinking(false)
            sidebar.append_text("Approval needed: " .. tool, "action", meta)
            sidebar.append_text(args_str, "code", meta)
            sidebar.append_text("", "approval_prompt", { tool = tool, reply_fn = current_reply_fn })
            return true
          end

          if channel == "reasoning" then
            if meta.chunk then
              sidebar.append_text(content, "thinking_chunk", meta)
            else
              sidebar.append_text(content, "thinking", meta)
            end
          elseif channel == "response_draft" then
            if meta.chunk then
              sidebar.append_text(content, "response_draft_chunk", meta)
            end
          elseif channel == "response_discard" then
            sidebar.append_text("", "response_discard", meta)
          elseif channel == "response" then
            if phase == "done" or meta.final then
              sidebar.set_thinking(false)
            end
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
            if phase == "done" or meta.final then
              sidebar.set_thinking(false)
              final_error_already_shown = true
            end
            sidebar.append_text(content, "error", meta)
          end
          return true
        end

        sidebar.append_text(goal, "user")
        local agent_mode = M.get_mode()
        local retried = false
        local final_error_already_shown = false

        local function do_agent_request()
          local fresh = M._conversation_fresh
          if fresh then M._conversation_fresh = false end
          rpc.request(M._cfg.socket, {
            cmd = "agent_task",
            root = root,
            goal = goal,
            session_id = session_id,
            mode = agent_mode,
            fresh_conversation = fresh,
          }, function(ev, reply)
            if ev.status == "eof" then return end

            -- Auto-reconnect: if the daemon crashed mid-flight, respawn and retry once
            if rpc.is_connect_error(ev) and not retried then
              retried = true
              sidebar.append_text("Daemon connection lost — reconnecting...", "info")
              M._spawn_daemon(function()
                do_agent_request()
              end)
              return
            end

            if handle_event(ev, reply) then return end
            -- Error from daemon final result: show only if we didn't already show a streamed final error
            if ev.ok == false then
              sidebar.set_thinking(false)
              if not final_error_already_shown then
                local err = ev.error or (ev.data and ev.data.error) or ""
                local detail = ev.detail and (" (" .. ev.detail .. ")") or ""
                if err ~= "" then
                  sidebar.append_text(err .. detail, "error")
                else
                  sidebar.append_text("La tâche a échoué.", "error")
                end
              end
            end
            -- When run is done: optionally reload history (default off to avoid full wipe; timeline = source of truth)
            if ev.ok ~= nil and M._cfg.reload_sidebar_on_run_done then
              vim.schedule(function()
                M.reload_history(session_id)
              end)
            end
          end, { stream = true })
        end

        do_agent_request()
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

-- ── SGNew: start a fresh conversation for current project ──
vim.api.nvim_create_user_command("SGNew", function()
  local root = project_root()
  local session_id = vim.fn.sha256(root)
  M._last_root = root
  M._last_session_id = session_id
  M._conversation_fresh = true
  rpc.request(M._cfg.socket, { cmd = "reset_session", session_id = session_id }, function(ev)
    if ev and ev.ok then
      notify("Nouvelle conversation démarrée.")
    else
      notify("Impossible de réinitialiser la session.", vim.log.levels.ERROR)
    end
  end)
end, {})

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

-- ── SGMode: set agent mode (auto | review) ──
vim.api.nvim_create_user_command("SGMode", function(opts)
  local arg = (opts.args or ""):gsub("%s+", ""):lower()
  if arg == "" then
    notify("ShellGeist mode: " .. M.get_mode(), vim.log.levels.INFO)
    return
  end
  if arg ~= "auto" and arg ~= "review" then
    notify("SGMode: use 'auto' or 'review'", vim.log.levels.WARN)
    return
  end
  M.set_mode(arg)
  notify("ShellGeist mode: " .. arg, vim.log.levels.INFO)
end, { nargs = "?", complete = function() return { "auto", "review" } end })

-- ── SGReview: show git diff in review panel (accept/reject/stage) ──
-- Bang (!) opens flat diff tab instead of inline conflict view.

vim.api.nvim_create_user_command("SGReview", function(opts)
  local cwd = vim.loop.cwd()
  local root = git_root(cwd)
  if not root then
    notify("Not inside a git repository (cwd: " .. cwd .. ")", vim.log.levels.WARN)
    return
  end
  local file = opts.args ~= "" and opts.args or nil
  local use_inline = not opts.bang  -- default: inline conflict view

  -- Build the git diff command
  local diff_cmd
  if file then
    diff_cmd = { "git", "-C", root, "diff", "--", file }
  else
    diff_cmd = { "git", "-C", root, "diff" }
  end

  local diff_out = vim.fn.systemlist(diff_cmd)
  if vim.v.shell_error ~= 0 or not diff_out or #diff_out == 0 then
    -- Try staged diff too
    local staged_cmd
    if file then
      staged_cmd = { "git", "-C", root, "diff", "--cached", "--", file }
    else
      staged_cmd = { "git", "-C", root, "diff", "--cached" }
    end
    diff_out = vim.fn.systemlist(staged_cmd)
    if not diff_out or #diff_out == 0 then
      notify("No changes to review", vim.log.levels.INFO)
      return
    end
  end

  local diff_text = table.concat(diff_out, "\n")

  -- Extract file from diff header if not specified
  local review_file = file
  if not review_file then
    for _, line in ipairs(diff_out) do
      local f = line:match("^diff %-%-git a/(.-) b/")
      if f then
        review_file = f
        break
      end
    end
  end

  if use_inline then
    -- Inline conflict view (avante-style): show <<<<<<< / ======= / >>>>>>>
    -- directly in the source buffer with co/ct/cb/ca keybindings
    local ok_c, err_c = pcall(function()
      conflict.show_from_diff(diff_text, root)
    end)
    if ok_c then
      notify("Review (inline): " .. (review_file or "all changes") .. " — co=original ct=accept cb=both ca=all ]x/[x=nav")
    else
      notify("Inline review failed, falling back to diff tab: " .. tostring(err_c), vim.log.levels.WARN)
      -- Fall back to diff tab
      local ok_d, err_d = pcall(function()
        diffview.preview(diff_text, {
          root = root,
          file = review_file or "",
          patch = diff_text,
          instruction = "review",
        })
      end)
      if not ok_d then
        open_diff_fallback(diff_text, "ShellGeist Review")
      end
    end
  else
    -- Flat diff tab (SGReview! with bang)
    local ok, err = pcall(function()
      diffview.preview(diff_text, {
        root = root,
        file = review_file or "",
        patch = diff_text,
        instruction = "review",
      })
    end)
    if ok then
      notify("Review (tab): " .. (review_file or "all changes") .. " — a=apply s=stage R=restore q=close")
    else
      notify("Review failed: " .. tostring(err), vim.log.levels.ERROR)
      open_diff_fallback(diff_text, "ShellGeist Review")
    end
  end
end, { bang = true, nargs = "?", complete = "file" })

-- ── SGDashboardToggle (alias for sidebar toggle) ──

vim.api.nvim_create_user_command("SGDashboardToggle", function()
  sidebar.toggle()
end, {})

-- ── SGDiagnostic: print environment / connection health ──

vim.api.nvim_create_user_command("SGDiagnostic", function()
  local lines = {}
  local function add(label, value)
    table.insert(lines, string.format("%-24s %s", label, tostring(value)))
  end

  add("socket", M._cfg.socket)
  add("mode", M.get_mode())
  add("sidebar_open", tostring(sidebar.is_open()))
  add("project_root", project_root())
  add("OPENAI_BASE_URL", vim.env.OPENAI_BASE_URL or "(not set)")
  add("OPENAI_API_KEY", vim.env.OPENAI_API_KEY and "(set)" or "(not set)")
  add("SHELLGEIST_MODEL", vim.env.SHELLGEIST_MODEL or "(default)")
  add("SHELLGEIST_DEBUG", vim.env.SHELLGEIST_DEBUG or "0")

  -- Probe daemon
  local socket = M._cfg.socket
  local socket_exists = vim.fn.filereadable(socket) == 1 or vim.loop.fs_stat(socket) ~= nil
  add("socket_exists", tostring(socket_exists))

  if socket_exists then
    rpc.request(socket, { cmd = "ping" }, function(ev)
      if ev.ok then
        add("daemon", "running (ping OK)")
      else
        add("daemon", "not responding (" .. tostring(ev.error or "unknown") .. ")")
      end

      vim.schedule(function()
        vim.cmd("tabnew")
        local buf = vim.api.nvim_get_current_buf()
        vim.api.nvim_buf_set_name(buf, "ShellGeist Diagnostic")
        vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
        vim.bo[buf].buftype = "nofile"
        vim.bo[buf].bufhidden = "wipe"
        vim.bo[buf].swapfile = false
        vim.bo[buf].modifiable = false
      end)
    end)
  else
    add("daemon", "socket not found")
    vim.cmd("tabnew")
    local buf = vim.api.nvim_get_current_buf()
    vim.api.nvim_buf_set_name(buf, "ShellGeist Diagnostic")
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, lines)
    vim.bo[buf].buftype = "nofile"
    vim.bo[buf].bufhidden = "wipe"
    vim.bo[buf].swapfile = false
    vim.bo[buf].modifiable = false
  end
end, {})

return M
