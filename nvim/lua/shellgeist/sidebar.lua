local M = {}

M.layout = nil
M.chat = nil
M.prompt = nil
M._streaming_assistant = false
M._current_status = "request"

local chat_ns = vim.api.nvim_create_namespace("shellgeist_chat")
local spinner_frames = { "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏" }
local spinner_timer = nil
local Layout = nil
local Input = nil
local Popup = nil

local function buf_valid(bufnr)
  return type(bufnr) == "number" and bufnr > 0 and vim.api.nvim_buf_is_valid(bufnr)
end

local function win_valid(winid)
  return type(winid) == "number" and winid > 0 and vim.api.nvim_win_is_valid(winid)
end

local function sanitize_text(text)
  local s = tostring(text or "")
  -- Keep newlines/tabs, remove carriage return + control chars.
  s = s:gsub("\r", "")
  s = s:gsub("[%z\1-\8\11\12\14-\31\127]", "")
  return s
end

local function compact_line(text, max_len)
  local one = sanitize_text(text):gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", "")
  local n = max_len or 140
  if #one > n then
    return one:sub(1, n) .. "…"
  end
  return one
end

local function is_meta_thinking_noise(text)
  local t = sanitize_text(text):lower()
  local patterns = {
    "tool_use",
    "formatting",
    "instructions should be formatted",
    "you are correct",
    "xml syntax",
    "json valide",
    "persistent misunderstanding",
    "regarding the use of",
  }
  for _, p in ipairs(patterns) do
    if t:find(p, 1, true) then
      return true
    end
  end
  return false
end

local function summarize_observation(text)
  local raw = compact_line(text or "", 240)
  local l = raw:lower()

  if l:find("module not found") then
    return "Dependency missing in environment: " .. raw
  end
  if l:find("unexpected keyword argument") then
    return "Tool API mismatch: " .. raw
  end
  if l:find("invalid_session_id") then
    return "Shell session ID invalid; agent must reuse latest valid session."
  end
  if l:find("error: empty command") then
    return "Tool call missing command argument."
  end
  if l:find("blocked_repeat_tool") then
    return "Repeated failing tool call blocked (loop guard)."
  end
  if l:find("pip: command not found") or l:find("pip3: command not found") then
    return "pip unavailable in this nix shell; use python3.withPackages or nix shell packages only."
  end
  if l:find("externally%-managed%-environment") then
    return "Nix store is immutable; direct pip install is blocked in this environment."
  end

  return raw
end

local function compact_code_block(text)
  local s = sanitize_text(text)
  if s == "" then
    return { "```", "(empty)", "```" }
  end
  local max_chars = 1800
  local truncated = s
  local clipped = false
  if #truncated > max_chars then
    truncated = truncated:sub(1, max_chars)
    clipped = true
  end
  local lines = vim.split(truncated, "\n", { plain = true })
  local max_lines = 28
  if #lines > max_lines then
    lines = vim.list_slice(lines, 1, max_lines)
    clipped = true
  end
  local out = { "```" }
  vim.list_extend(out, lines)
  if clipped then
    table.insert(out, "… (truncated)")
  end
  table.insert(out, "```")
  return out
end

local function phase_hl_for_type(type)
  if type == "thinking" or type == "thought" then return "ShellGeistThinkingGray" end
  if type == "action" then return "ShellGeistToolActionBg" end
  if type == "code" then return "ShellGeistToolCodeBg" end
  if type == "observation" then return "ShellGeistToolResultBg" end
  return nil
end

local function set_prompt_top(text)
  if not M.prompt or not M.prompt.border then
    return
  end
  pcall(function()
    M.prompt.border:set_text("top", text, "center")
  end)
end

local function notify_err(msg)
  vim.schedule(function()
    vim.notify("ShellGeist: " .. tostring(msg), vim.log.levels.ERROR, { title = "ShellGeist" })
  end)
end

local function ensure_nui()
  if Layout and Input and Popup then
    return true
  end
  local ok_layout, layout_mod = pcall(require, "nui.layout")
  local ok_input, input_mod = pcall(require, "nui.input")
  local ok_popup, popup_mod = pcall(require, "nui.popup")
  if not (ok_layout and ok_input and ok_popup) then
    local why = {
      ok_layout and nil or "nui.layout",
      ok_input and nil or "nui.input",
      ok_popup and nil or "nui.popup",
    }
    notify_err("missing dependency: " .. table.concat(vim.tbl_filter(function(v) return v ~= nil end, why), ", "))
    return false
  end
  Layout = layout_mod
  Input = input_mod
  Popup = popup_mod
  return true
end

local function reset_state()
  if spinner_timer then
    spinner_timer:stop()
    spinner_timer:close()
    spinner_timer = nil
  end
  M.layout = nil
  M.chat = nil
  M.prompt = nil
  M._streaming_assistant = false
  M._current_status = "request"
end

local function dispatch_sgagent(value)
  local goal = sanitize_text(value):gsub("\n", " "):gsub("^%s+", ""):gsub("%s+$", "")
  if goal == "" then
    return
  end

  local ok_mod, mod = pcall(require, "shellgeist")
  if ok_mod and mod and type(mod.run_agent) == "function" then
    local ok_run, run_err = pcall(mod.run_agent, goal)
    if not ok_run then
      notify_err("failed to submit request: " .. tostring(run_err))
    end
    return
  end

  -- Fallback: Ex command path.
  local ok_cmd, cmd_err = pcall(function()
    vim.api.nvim_cmd({ cmd = "SGAgent", args = { goal } }, {})
  end)
  if not ok_cmd then
    notify_err("failed to submit request: " .. tostring(cmd_err))
  end
end

local function submit_from_prompt(prompt)
  if not prompt or not buf_valid(prompt.bufnr) then
    return
  end

  local ok_lines, lines = pcall(vim.api.nvim_buf_get_lines, prompt.bufnr, 0, -1, false)
  if not ok_lines then
    return
  end

  local value = sanitize_text(table.concat(lines or {}, "\n"))
  if value ~= "" then
    vim.schedule(function()
      dispatch_sgagent(value)
    end)
  end

  -- Keep the sidebar mounted and just clear the prompt content.
  pcall(vim.api.nvim_buf_set_lines, prompt.bufnr, 0, -1, false, { "" })
  if win_valid(prompt.winid) then
    pcall(vim.api.nvim_set_current_win, prompt.winid)
    pcall(vim.cmd, "startinsert")
  end
end

function M.is_open()
  return M.layout and M.layout.winid and win_valid(M.layout.winid)
end

function M.toggle()
  if M.is_open() then
    pcall(function()
      M.layout:unmount()
    end)
    reset_state()
  else
    M.open()
  end
end

function M.open()
  if M.is_open() then return end
  if not ensure_nui() then return end

  local ok, err = pcall(function()
    -- Define vibrant highlights
    vim.api.nvim_set_hl(0, "ShellGeistBorder", { fg = "#3b82f6" }) -- Blue
    vim.api.nvim_set_hl(0, "ShellGeistThinking", { fg = "#10b981", bold = true }) -- Green for active spinner state
    vim.api.nvim_set_hl(0, "ShellGeistThinkingGray", { fg = "#9ca3af" })
    vim.api.nvim_set_hl(0, "ShellGeistHeader", { fg = "#3b82f6" }) -- Blue for User/Assistant labels
    vim.api.nvim_set_hl(0, "ShellGeistBody", { fg = "#ffffff" }) -- White for message text
    vim.api.nvim_set_hl(0, "ShellGeistToolActionBg", { bg = "#0b1f3a" })
    vim.api.nvim_set_hl(0, "ShellGeistToolCodeBg", { bg = "#1f1636" })
    vim.api.nvim_set_hl(0, "ShellGeistToolResultBg", { bg = "#132a1b" })

    local chat = Popup({
      enter = false,
      border = {
        style = "rounded",
        highlight = "ShellGeistBorder",
        text = { top = " ShellGeist ", top_align = "center" },
      },
      buf_options = {
        filetype = "markdown",
        buftype = "nofile",
      },
      win_options = {
        wrap = true,
        winhighlight = "Normal:ShellGeistBody,FloatBorder:ShellGeistBorder",
      }
    })

    local prompt
    prompt = Input({
      border = {
        style = "rounded",
        highlight = "ShellGeistBorder",
        text = { top = " [Request] ", top_align = "center" },
      },
      win_options = {
        winhighlight = "Normal:Normal,FloatBorder:ShellGeistBorder",
      },
    }, {
      on_submit = function(value)
        -- NOTE: nui.input closes itself on <CR> by default.
        -- We override <CR> keymaps below to submit without unmounting.
        if value and value ~= "" then
          vim.schedule(function()
            dispatch_sgagent(value)
          end)
        end
      end,
    })

    -- Keep sidebar open on Enter: submit from prompt buffer instead of default close-on-submit.
    prompt:map("i", "<CR>", function()
      submit_from_prompt(prompt)
    end, { noremap = true, nowait = true })
    prompt:map("n", "<CR>", function()
      submit_from_prompt(prompt)
    end, { noremap = true, nowait = true })

    M.chat = chat
    M.prompt = prompt
    M._current_status = "request"

    -- Robust Layout configuration
    M.layout = Layout(
      {
        relative = "editor",
        position = {
          row = 0,
          col = "100%",
        },
        size = {
          width = 45,
          height = "100%",
        },
      },
      Layout.Box({
        Layout.Box(M.chat, { size = "88%" }),
        Layout.Box(M.prompt, { size = "12%" }),
      }, { dir = "col" })
    )

    M.layout:mount()
    M.render_welcome()
    
    -- Keymaps
    M.chat:map("n", "q", function() M.toggle() end, { noremap = true })
    M.prompt:map("n", "<Esc>", function()
      if M.chat and win_valid(M.chat.winid) then
        pcall(vim.api.nvim_set_current_win, M.chat.winid)
      end
    end, { noremap = true })
  end)
  if not ok then
    reset_state()
    notify_err("sidebar open failed: " .. tostring(err))
  end
end

function M.render_welcome()
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  M._streaming_assistant = false
  local lines = {
    "# ShellGeist Chat",
    "",
    "Ready to assist you.",
    "---",
    "",
  }
  pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, 0, -1, false, lines)
end

function M.append_text(text, msg_type, meta)
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  text = sanitize_text(text)
  meta = type(meta) == "table" and meta or {}
  
  -- Streaming: append to current assistant block without new header
  if msg_type == "assistant_chunk" or msg_type == "response_chunk" then
    local chunk = text
    if chunk == "" then return end
    local ok_count, line_count = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
    if not ok_count then return end
    local is_first = not M._streaming_assistant
    if is_first then
      M._streaming_assistant = true
    end
    if line_count == 0 then
      local first_nl = chunk:find("\n", 1, true)
      local first_line = first_nl and chunk:sub(1, first_nl - 1) or chunk
      local rest = first_nl and chunk:sub(first_nl + 1) or ""
      pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, -1, -1, false, { "#### 󰭻 Response: " .. first_line })
      if rest ~= "" then
        local rest_lines = vim.split(rest, "\n", { plain = true })
        pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, -1, -1, false, rest_lines)
      end
    else
      local last_idx = line_count - 1
      local ok_last, last_lines = pcall(vim.api.nvim_buf_get_lines, M.chat.bufnr, last_idx, last_idx + 1, false)
      if not ok_last then return end
      local last_line = (last_lines and last_lines[1]) or ""
      local newline_pos = chunk:find("\n", 1, true)
      if newline_pos then
        local first_part = chunk:sub(1, newline_pos - 1)
        local rest = chunk:sub(newline_pos + 1)
        if is_first then
          pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_idx, last_idx + 1, false, { last_line })
          pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, -1, -1, false, { "#### 󰭻 Response: " .. first_part })
        else
          pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_idx, last_idx + 1, false, { last_line .. first_part })
        end
        local rest_lines = vim.split(rest, "\n", { plain = true })
        pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, -1, -1, false, rest_lines)
      else
        if is_first then
          pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, -1, -1, false, { "#### 󰭻 Response: " .. chunk })
        else
          pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_idx, last_idx + 1, false, { last_line .. chunk })
        end
      end
    end
    -- Scroll
    local winid = M.chat.winid
    if win_valid(winid) then
      local ok_lines, count = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
      if ok_lines then
        pcall(vim.api.nvim_win_set_cursor, winid, { count, 0 })
      end
    end
    return
  end

  -- End of streaming assistant block
  if msg_type ~= "assistant_chunk" and msg_type ~= "response_chunk" then
    M._streaming_assistant = false
  end

  -- Keep info as status-only, but render explicit thinking/action/code/result phases.
  if msg_type == "info" then
    local icon = ""
    icon = "󰋽"
    
    M._current_status = type
    local status_text = string.format(" %s %s ", icon, text:gsub("\n", " "):sub(1, 40))
    set_prompt_top(status_text)
    return
  end

  local prefix = ""
  local lines = {}
  local target_file = nil
  if type(meta.file) == "string" and meta.file ~= "" then
    target_file = meta.file
  end

  if msg_type == "user" then
    prefix = "##  User: "
    lines = { prefix .. text }
  elseif msg_type == "assistant" or msg_type == "response" then
    prefix = "#### 󰭻 Response: "
    lines = { prefix .. compact_line(text, 220) }
  elseif msg_type == "thinking" or msg_type == "thought" then
    if is_meta_thinking_noise(text) then
      return
    end
    prefix = "#### 󰋘 Thinking: "
    lines = { prefix .. compact_line(text, 160) }
  elseif msg_type == "action" then
    prefix = target_file and ("#### 󱔗 Action: " .. target_file .. " • ") or "#### 󱔗 Action: "
    lines = { prefix .. compact_line(text, 180) }
  elseif msg_type == "observation" then
    prefix = target_file and ("#### 󱍬 Result: " .. target_file .. " • ") or "#### 󱍬 Result: "
    lines = { prefix .. summarize_observation(text) }
  elseif msg_type == "code" then
    prefix = target_file and ("#### 󰨞 Code: " .. target_file) or "#### 󰨞 Code"
    lines = { prefix }
    vim.list_extend(lines, compact_code_block(text))
  elseif msg_type == "error" then
    prefix = "### 󰅚 Error: "
    lines = { prefix .. compact_line(text, 220) }
  else
    return
  end

  table.insert(lines, "")

  local ok_start, start_line = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
  if not ok_start then return end
  local ok_set = pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, -1, -1, false, lines)
  if not ok_set then return end

  -- Highlight
  local prefix_len = #prefix
  local phase_hl = phase_hl_for_type(msg_type)
  for i = 1, #lines do
    local line_idx = start_line + i - 1
    local line = lines[i]
    if phase_hl and line ~= "" then
      pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, phase_hl, line_idx, 0, -1)
    end
    if (msg_type == "thinking" or msg_type == "thought") and line ~= "" then
      goto continue
    end
    if i == 1 and prefix_len > 0 and line ~= "" then
      pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "ShellGeistHeader", line_idx, 0, prefix_len)
      if #line > prefix_len then
        pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "ShellGeistBody", line_idx, prefix_len, -1)
      end
    elseif line ~= "" then
      pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "ShellGeistBody", line_idx, 0, -1)
    end
    ::continue::
  end

  -- scroll
  local winid = M.chat.winid
  if win_valid(winid) then
    local ok_lines, count = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
    if ok_lines then
      pcall(vim.api.nvim_win_set_cursor, winid, { count, 0 })
    end
  end
end

function M.set_thinking(is_thinking)
  if not M.prompt or not M.prompt.border then return end
  
  if is_thinking then
    if spinner_timer then return end
    local frame = 1
    spinner_timer = vim.loop.new_timer()
    spinner_timer:start(0, 100, vim.schedule_wrap(function()
      if not M.prompt or not M.prompt.border then
        if spinner_timer then spinner_timer:stop() spinner_timer:close() spinner_timer = nil end
        return
      end
      -- Only update if no tool status is active
      if M._current_status == "request" or M._current_status == "thinking" then
        M._current_status = "thinking"
        set_prompt_top(" Thinking " .. spinner_frames[frame] .. " ")
      end
      frame = (frame % #spinner_frames) + 1
    end))
  else
    if spinner_timer then
      spinner_timer:stop()
      spinner_timer:close()
      spinner_timer = nil
    end
    M._current_status = "request"
    set_prompt_top(" [Request] ")
  end
end

return M
