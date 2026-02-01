local M = {}

-- Escape helper (useful if you ever put dynamic text into statusline/lualine)
local function statusline_escape(s)
  s = tostring(s or "")
  s = s:gsub("%%", "%%%%")          -- % must be doubled for statusline
  s = s:gsub("[%z\1-\31\127]", "")  -- drop control chars
  return s
end

-- Stream text into a buffer like a "typewriter":
-- - appends to the last line
-- - handles '\n' by creating a new line
function M.stream(buf, text)
  if not buf or not vim.api.nvim_buf_is_valid(buf) then
    return
  end
  text = tostring(text or "")

  -- Ensure buffer has at least one line
  local lines = vim.api.nvim_buf_get_lines(buf, 0, -1, false)
  if #lines == 0 then
    vim.api.nvim_buf_set_lines(buf, 0, -1, false, { "" })
  end

  -- append helper
  local function append_chunk(chunk)
    local last = vim.api.nvim_buf_line_count(buf) - 1
    local cur = vim.api.nvim_buf_get_lines(buf, last, last + 1, false)[1] or ""
    vim.api.nvim_buf_set_lines(buf, last, last + 1, false, { cur .. chunk })
  end

  -- newline helper
  local function newline()
    vim.api.nvim_buf_set_lines(buf, -1, -1, false, { "" })
  end

  for c in text:gmatch(".") do
    if c == "\n" then
      newline()
    else
      append_chunk(c)
    end
    vim.wait(10)
  end
end

M.statusline_escape = statusline_escape

return M
