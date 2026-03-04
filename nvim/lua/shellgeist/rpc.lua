local M = {}

local function safe_cb(cb, ev, reply)
  local ok, err = pcall(cb, ev, reply)
  if not ok then
    vim.schedule(function()
      vim.notify("ShellGeist RPC callback error: " .. tostring(err), vim.log.levels.ERROR, { title = "ShellGeist" })
    end)
  end
end

local function decode_json(line)
  local ok, obj = pcall(vim.json.decode, line, { luanil = { object = true, array = true } })
  if ok and type(obj) == "table" then
    return obj
  end
  return nil, "bad_json"
end

--- Check whether an RPC error is a connect / pipe failure that hints
--- the daemon process crashed or was stopped.
---@param ev table  result event
---@return boolean
function M.is_connect_error(ev)
  if ev.ok then return false end
  local err = ev.error or ""
  return err == "connect_failed"
      or err == "rpc_pipe_failed"
      or err == "write_failed"
end

function M.request(sock_path, payload, cb, opts)
  opts = opts or {}
  cb = cb or function(_) end

  local pipe = vim.loop.new_pipe(false)
  if not pipe then
    vim.schedule(function()
      cb({ type = "result", ok = false, error = "rpc_pipe_failed" })
    end)
    return
  end

  -- Reply function: allows the callback to write back on the same pipe (for review mode approval)
  local reply_fn = function(response_payload)
    if pipe and not done then
      local line = vim.json.encode(response_payload) .. "\n"
      pipe:write(line, function(_werr) end)
    end
  end

  local done = false
  local buf = ""

  local function finish(ev)
    if done then
      return
    end
    done = true

    pcall(pipe.read_stop, pipe)
    pcall(pipe.close, pipe)

    vim.schedule(function()
      safe_cb(cb, ev)
    end)
  end

  pipe:connect(sock_path, function(err)
    if err then
      finish({ type = "result", ok = false, error = "connect_failed", detail = tostring(err) })
      return
    end

    local line = vim.json.encode(payload) .. "\n"

    pipe:write(line, function(werr)
      if werr then
        finish({ type = "result", ok = false, error = "write_failed", detail = tostring(werr) })
        return
      end

      pipe:read_start(function(rerr, chunk)
        if rerr then
          finish({ type = "result", ok = false, error = "read_failed", detail = tostring(rerr) })
          return
        end

        if not chunk then
          -- EOF
          if buf ~= "" then
            local obj, derr = decode_json(buf)
            if obj then
              if not opts.stream then
                finish(obj)
                return
              end
              vim.schedule(function() safe_cb(cb, obj, reply_fn) end)
            end
          end
          finish({ type = "result", ok = true, status = "eof" })
          return
        end

        buf = buf .. chunk
        while true do
          local nl = buf:find("\n", 1, true)
          if not nl then break end
          
          local one = buf:sub(1, nl - 1)
          buf = buf:sub(nl + 1)
          
          local obj, derr = decode_json(one)
          if obj then
            if not opts.stream then
              -- Non-streaming: let finish() be the sole caller of cb
              -- to avoid calling cb twice (once here, once in finish).
              finish(obj)
              return
            end
            vim.schedule(function() safe_cb(cb, obj, reply_fn) end)
          else
            if not opts.stream then
              finish({ type = "result", ok = false, error = derr or "bad_json" })
              return
            end
          end
        end
      end)
    end)
  end)
end

return M
