local M = {}

local rpc = require("shellgeist.rpc")

local function notify(msg, level)
  vim.schedule(function()
    vim.notify(msg, level or vim.log.levels.INFO, { title = "ShellGeist" })
  end)
end

local function get_cfg()
  local ok, sg = pcall(require, "shellgeist")
  if ok and sg and sg._cfg then
    return sg._cfg
  end
  return { socket = vim.fn.expand("~/.cache/shellgeist.sock") }
end

local function buf_map(buf, lhs, fn, desc)
  vim.keymap.set("n", lhs, fn, { buffer = buf, silent = true, noremap = true, desc = desc })
end

local function buf_cmd(buf, name, fn, desc)
  vim.api.nvim_buf_create_user_command(buf, name, fn, { desc = desc })
end

function M.preview(diff, ctx)
  vim.schedule(function()
    vim.cmd("tabnew")
    local buf = vim.api.nvim_get_current_buf()

    vim.api.nvim_buf_set_lines(buf, 0, -1, false, vim.split(diff or "", "\n", { plain = true }))
    vim.bo[buf].filetype = "diff"
    vim.bo[buf].buftype = "nofile"
    vim.bo[buf].bufhidden = "wipe"
    vim.bo[buf].swapfile = false

    vim.b[buf].shellgeist_ctx = ctx or {}

    notify("ShellGeist diff: a=apply  F=full  s=stage  R=restore  q=close  (:SGAccept/:SGReject)")

    local function close()
      vim.schedule(function()
        if vim.api.nvim_buf_is_valid(buf) then
          -- tabclose touches UI, so schedule it too
          pcall(vim.cmd, "tabclose")
        end
      end)
    end

    local function apply_patch()
      local cfg = get_cfg()
      local c = vim.b[buf].shellgeist_ctx or {}
      if not c.root or not c.file or not c.patch then
        notify("missing ctx (root/file/patch) — try 'F' if full_replace exists", vim.log.levels.ERROR)
        return
      end

      notify("apply: sending…")
      rpc.request(cfg.socket, {
        cmd = "edit_apply",
        root = c.root,
        file = c.file,
        patch = c.patch,
        instruction = c.instruction or "apply",
        backup = true,
        stage = false,
      }, function(ev)
        if ev.type == "result" and ev.ok then
          notify("applied: " .. c.file)
          close()
          return
        end

        if ev.type == "result" then
          local msg = (ev.error or "apply_failed") .. (ev.detail and (": " .. ev.detail) or "")
          notify(msg, vim.log.levels.ERROR)

          -- UX: si patch mismatch, on suggère immédiatement la cause
          if msg:match("mismatch") or msg:match("context") then
            notify("patch stale: le fichier a changé. Relance :SGEdit pour régénérer le diff.", vim.log.levels.WARN)
          end
        end
      end)
    end

    local function apply_full()
      local cfg = get_cfg()
      local c = vim.b[buf].shellgeist_ctx or {}
      if not c.root or not c.file or not c.full_replace then
        notify("missing ctx (root/file/full_replace)", vim.log.levels.ERROR)
        return
      end

      notify("apply full: sending…")
      rpc.request(cfg.socket, {
        cmd = "edit_apply_full",
        root = c.root,
        file = c.file,
        text = c.full_replace,
        instruction = c.instruction or "apply_full",
        backup = true,
        stage = false,
      }, function(ev)
        if ev.type == "result" and ev.ok then
          notify("applied(full): " .. c.file)
          close()
        elseif ev.type == "result" then
          notify((ev.error or "apply_full_failed") .. (ev.detail and (": " .. ev.detail) or ""), vim.log.levels.ERROR)
        end
      end)
    end

    local function git_stage()
      local cfg = get_cfg()
      local c = vim.b[buf].shellgeist_ctx or {}
      if not c.root or not c.file then
        notify("missing ctx (root/file)", vim.log.levels.ERROR)
        return
      end

      notify("stage: sending…")
      rpc.request(cfg.socket, { cmd = "git_add", root = c.root, file = c.file }, function(ev)
        if ev.type == "result" and ev.ok then
          notify("staged: " .. c.file)
        elseif ev.type == "result" then
          notify((ev.error or "git_add_failed") .. (ev.detail and (": " .. ev.detail) or ""), vim.log.levels.ERROR)
        end
      end)
    end

    local function git_restore()
      local cfg = get_cfg()
      local c = vim.b[buf].shellgeist_ctx or {}
      if not c.root or not c.file then
        notify("missing ctx (root/file)", vim.log.levels.ERROR)
        return
      end

      notify("restore: sending…")
      rpc.request(cfg.socket, { cmd = "git_restore", root = c.root, file = c.file }, function(ev)
        if ev.type == "result" and ev.ok then
          notify("restored: " .. c.file)
          close()
        elseif ev.type == "result" then
          notify((ev.error or "git_restore_failed") .. (ev.detail and (": " .. ev.detail) or ""), vim.log.levels.ERROR)
        end
      end)
    end

    -- Keymaps
    buf_map(buf, "q", close, "ShellGeist: Close (reject)")
    buf_map(buf, "a", apply_patch, "ShellGeist: Apply patch")
    buf_map(buf, "F", apply_full, "ShellGeist: Apply full replace")
    buf_map(buf, "s", git_stage, "ShellGeist: Git stage file")
    buf_map(buf, "R", git_restore, "ShellGeist: Git restore file")

    -- Buffer commands (stable, non-volatile)
    buf_cmd(buf, "SGAccept", function() apply_patch() end, "ShellGeist: Apply patch")
    buf_cmd(buf, "SGFull", function() apply_full() end, "ShellGeist: Apply full replace")
    buf_cmd(buf, "SGStage", function() git_stage() end, "ShellGeist: Git stage file")
    buf_cmd(buf, "SGRestore", function() git_restore() end, "ShellGeist: Git restore file")
    buf_cmd(buf, "SGReject", function() close() end, "ShellGeist: Close (reject)")
  end)
end

return M
