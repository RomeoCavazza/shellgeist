-- This file is executed when the plugin is loaded (runtimepath).
-- Keep it tiny; delegate to lua/shellgeist/init.lua.

if vim.g.loaded_shellgeist == 1 then
    return
  end
  vim.g.loaded_shellgeist = 1
  
  require("shellgeist").setup()
  