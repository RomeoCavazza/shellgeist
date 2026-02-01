local M = {}

function M.open()
  vim.cmd("vsplit")
  vim.cmd("enew")
  vim.bo.filetype = "shellgeist"
  vim.bo.buftype = "nofile"
  vim.bo.bufhidden = "wipe"
  vim.bo.swapfile = false

  vim.api.nvim_buf_set_lines(0, 0, -1, false, {
    "# ShellGeist",
    "",
    ":SGPing",
    ":SGChat <msg>",
    ":SGPlan <goal>",
    ":SGEdit <file> <instruction...>",
    ":SGShell <task...>",
  })
end

return M
