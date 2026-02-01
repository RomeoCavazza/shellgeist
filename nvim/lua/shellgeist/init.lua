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
      on_ok(ev)
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

-- Commands
vim.api.nvim_create_user_command("SGSidebar", function()
  sidebar.open()
end, {})

vim.api.nvim_create_user_command("SGPing", function()
  rpc.request(M._cfg.socket, { cmd = "ping" }, function(ev)
    handle_result(ev, function()
      notify("ok")
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
