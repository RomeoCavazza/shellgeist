local M = {}

local function decode_json(line)
  local ok, obj = pcall(vim.json.decode, line, { luanil = { object = true, array = true } })
  if ok and type(obj) == "table" then
    return obj
  end
  return nil, "bad_json"
end

function M.request(sock_path, payload, cb)
  cb = cb or function(_) end

  local pipe = vim.loop.new_pipe(false)
  if not pipe then
    vim.schedule(function()
      cb({ type = "result", ok = false, error = "rpc_pipe_failed" })
    end)
    return
  end

  local done = false
  local buf = ""

  local function finish(ev)
    if done then
      return
    end
    done = true

    -- libuv cleanup is OK here
    pcall(pipe.read_stop, pipe)
    pcall(pipe.close, pipe)

    -- CRITICAL: nvim API must run on main loop
    vim.schedule(function()
      cb(ev)
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
          -- EOF before newline
          if buf ~= "" then
            local obj, derr = decode_json(buf)
            if obj then
              finish(obj)
            else
              finish({ type = "result", ok = false, error = derr or "bad_json" })
            end
          else
            finish({ type = "result", ok = false, error = "eof" })
          end
          return
        end

        buf = buf .. chunk
        local nl = buf:find("\n", 1, true)
        if nl then
          local one = buf:sub(1, nl - 1)
          local obj, derr = decode_json(one)
          if obj then
            finish(obj)
          else
            finish({ type = "result", ok = false, error = derr or "bad_json" })
          end
        end
      end)
    end)
  end)
end

return M
