-- ╔═══════════════════════════════════════════════════════════════════════╗
-- ║  ShellGeist sidebar — avante-inspired chat UI                       ║
-- ╚═══════════════════════════════════════════════════════════════════════╝
local M = {}

M.layout = nil
M.chat = nil
M.prompt = nil
M._streaming_assistant = false
M._current_status = "request"

local chat_ns = vim.api.nvim_create_namespace("shellgeist_chat")
local spinner_frames = { "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏" }
local spinner_timer = nil
local Layout, Input, Popup = nil, nil, nil

-- ── helpers ────────────────────────────────────────────────────────────

local function buf_valid(b) return type(b) == "number" and b > 0 and vim.api.nvim_buf_is_valid(b) end
local function win_valid(w) return type(w) == "number" and w > 0 and vim.api.nvim_win_is_valid(w) end

local function sanitize(text)
  local s = tostring(text or "")
  s = s:gsub("\r", "")
  s = s:gsub("[%z\1-\8\11\12\14-\31\127]", "")
  return s
end

local function compact(text, max)
  local one = sanitize(text):gsub("%s+", " "):gsub("^%s+", ""):gsub("%s+$", "")
  max = max or 140
  if #one > max then return one:sub(1, max) .. "…" end
  return one
end

local function is_noise(text)
  local t = sanitize(text):lower()
  for _, p in ipairs({
    "tool_use", "formatting", "instructions should be formatted",
    "you are correct", "xml syntax", "json valide",
    "persistent misunderstanding", "regarding the use of",
  }) do
    if t:find(p, 1, true) then return true end
  end
  return false
end

local function summarize_obs(text)
  local raw = compact(text, 240)
  local l = raw:lower()
  if l:find("module not found") then return " Dependency missing: " .. raw end
  if l:find("unexpected keyword argument") then return " API mismatch: " .. raw end
  if l:find("invalid_session_id") then return " Session ID invalid" end
  if l:find("blocked_repeat_tool") then return " Repeated failing call blocked" end
  if l:find("pip: command not found") or l:find("externally%-managed") then
    return " pip blocked in nix — use withPackages"
  end
  return raw
end

-- ── highlight definitions ──────────────────────────────────────────────

local function define_highlights()
  local hl = vim.api.nvim_set_hl
  -- Chrome
  hl(0, "SGBorder",       { fg = "#3b82f6", ctermfg = 69 })
  hl(0, "SGTitle",        { fg = "#1e222a", bg = "#98c379", ctermfg = 235, ctermbg = 114, bold = true })
  -- User
  hl(0, "SGUser",         { fg = "#98c379", ctermfg = 114, bold = true })
  hl(0, "SGUserBody",     { fg = "#abb2bf", ctermfg = 249 })
  -- Response
  hl(0, "SGResponse",     { fg = "#61afef", ctermfg = 75, bold = true })
  hl(0, "SGResponseBody", { fg = "#dcdfe4", ctermfg = 254 })
  -- Thinking
  hl(0, "SGThinking",     { fg = "#c678dd", ctermfg = 176, italic = true })
  -- Tool cards
  hl(0, "SGCardBorder",   { fg = "#5c6370", ctermfg = 241 })
  hl(0, "SGCardAction",   { fg = "#1e222a", bg = "#56b6c2", ctermfg = 235, ctermbg = 73, bold = true })
  hl(0, "SGCardCode",     { fg = "#1e222a", bg = "#61afef", ctermfg = 235, ctermbg = 75, bold = true })
  hl(0, "SGCardResult",   { fg = "#1e222a", bg = "#d19a66", ctermfg = 235, ctermbg = 173, bold = true })
  hl(0, "SGCardError",    { fg = "#1e222a", bg = "#e06c75", ctermfg = 235, ctermbg = 168, bold = true })
  hl(0, "SGCardBody",     { fg = "#abb2bf", ctermfg = 249 })
  -- Diff inline
  hl(0, "SGDiffAdd",      { fg = "#98c379", bg = "#2a3a2a", ctermfg = 114, ctermbg = 22 })
  hl(0, "SGDiffDel",      { fg = "#e06c75", bg = "#3a2a2a", ctermfg = 168, ctermbg = 52 })
  hl(0, "SGDiffHdr",      { fg = "#56b6c2", ctermfg = 73, bold = true })
  -- Success
  hl(0, "SGSuccess",      { fg = "#98c379", ctermfg = 114, bold = true })
  -- Error
  hl(0, "SGError",        { fg = "#e06c75", ctermfg = 168, bold = true })
  -- Spinner
  hl(0, "SGSpinner",      { fg = "#c678dd", ctermfg = 176, bold = true })
  -- Approval prompt
  hl(0, "SGApproval",     { fg = "#1e222a", bg = "#e5c07b", ctermfg = 235, ctermbg = 180, bold = true })
  hl(0, "SGApprovalKey",  { fg = "#e5c07b", ctermfg = 180, bold = true })
  -- Body default
  hl(0, "SGBody",         { fg = "#abb2bf", ctermfg = 249 })
end

-- Re-apply highlights when colorscheme changes (Neovim clears them)
vim.api.nvim_create_autocmd("ColorScheme", {
  group = vim.api.nvim_create_augroup("ShellGeistHighlights", { clear = true }),
  callback = define_highlights,
})

-- Define highlights immediately at load time
define_highlights()

-- ── buffer helpers ─────────────────────────────────────────────────────

--- Append lines to chat buffer; return the 0-based start line index.
local function buf_append(lines)
  if not M.chat or not buf_valid(M.chat.bufnr) then return nil end
  local ok_c, start = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
  if not ok_c then return nil end
  pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, -1, -1, false, lines)
  return start
end

--- Highlight a range of lines with a single hl group.
local function hl_range(start_idx, count, group)
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  for i = 0, count - 1 do
    pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, group, start_idx + i, 0, -1)
  end
end

--- Highlight a portion of one line.
local function hl_partial(line_idx, col_start, col_end, group)
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, group, line_idx, col_start, col_end)
end

--- Scroll chat window to the bottom (debounced for streaming perf).
local _scroll_pending = false
local function scroll_bottom()
  if _scroll_pending then return end
  _scroll_pending = true
  vim.defer_fn(function()
    _scroll_pending = false
    if not M.chat or not buf_valid(M.chat.bufnr) then return end
    local w = M.chat.winid
    if not win_valid(w) then return end
    local ok, n = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
    if ok then pcall(vim.api.nvim_win_set_cursor, w, { n, 0 }) end
  end, 30)
end

-- ── card builder (avante-style box-drawing) ────────────────────────────

local function build_card(header, body, max_body)
  max_body = max_body or 6
  local pad = math.max(1, 38 - #header)
  local top = "╭─ " .. header .. " " .. string.rep("─", pad)
  local bot = "╰" .. string.rep("─", 42)
  local out = { top }
  if body and #body > 0 then
    local visible = body
    local truncated = false
    if max_body > 0 and #body > max_body then
      visible = vim.list_slice(body, 1, max_body)
      truncated = true
    end
    for _, l in ipairs(visible) do
      table.insert(out, "│ " .. l)
    end
    if truncated then
      table.insert(out, "│ … (" .. (#body - max_body) .. " more lines)")
    end
  end
  table.insert(out, bot)
  return out
end

--- Check if text content looks like a unified diff.
--- Requires actual diff markers (@@, --- a/, +++ b/) — not just +/- lines.
local function is_diff_content(text)
  local has_hunk = text:find("^@@") or text:find("\n@@")
  local has_old  = text:find("^--- a/") or text:find("\n--- a/")
  local has_new  = text:find("^%+%+%+ b/") or text:find("\n%+%+%+ b/")
  if has_hunk then return true end
  if has_old and has_new then return true end
  return false
end

-- ── rendering functions ────────────────────────────────────────────────

local function render_user(text)
  local header = "󰀄 User"
  local body = compact(text, 200)
  local start = buf_append({ header, body, "" })
  if start then
    hl_range(start, 1, "SGUser")
    hl_range(start + 1, 1, "SGUserBody")
  end
  scroll_bottom()
end

local function render_response(text)
  -- Strip any "Thought:" prefix that was already emitted separately
  local body = sanitize(text)
  body = body:gsub("^%s*Thoughts?:%s*.-\n\n", "")
  body = body:gsub("^%s*Status:%s*DONE%s*$", "")
  body = vim.trim(body)
  if body == "" then return end

  local header = "󰚩 Assistant"
  local start = buf_append({ header })
  if start then hl_range(start, 1, "SGResponse") end
  -- Render full multi-line response body
  local body_lines = vim.split(body, "\n", { plain = true })
  local body_start = buf_append(body_lines)
  if body_start then
    hl_range(body_start, #body_lines, "SGResponseBody")
  end
  buf_append({ "" })
  scroll_bottom()
end

local function render_thinking(text)
  if is_noise(text) then return end
  local lines = vim.split(sanitize(text), "\n", { plain = true })
  local first = "󰋘 " .. (lines[1] or "")
  local start = buf_append({ first })
  if start then hl_range(start, 1, "SGThinking") end
  if #lines > 1 then
    local rest = vim.list_slice(lines, 2)
    local rs = buf_append(rest)
    if rs then hl_range(rs, #rest, "SGThinking") end
  end
  scroll_bottom()
end

local function render_action(text, meta)
  local tool = (meta and meta.tool) or ""
  local file = (meta and meta.file) or ""
  local label = tool
  if file ~= "" then label = label .. " • " .. file end
  local body_lines = {}
  local desc = compact(text, 200)
  if desc ~= "" then
    desc = desc:gsub("^Calling:%s*", "")
    if desc ~= "" and desc ~= tool then table.insert(body_lines, desc) end
  end
  local card = build_card("󱔗 " .. label, body_lines, 3)
  local start = buf_append(card)
  if start then
    hl_range(start, 1, "SGCardAction")
    for i = 2, #card do
      hl_partial(start + i - 1, 0, 4, "SGCardBorder")
      if i < #card and card[i] and #card[i] > 4 then
        hl_partial(start + i - 1, 4, -1, "SGCardBody")
      else
        hl_range(start + i - 1, 1, "SGCardBorder")
      end
    end
  end
  buf_append({ "" })
  scroll_bottom()
end

local function render_code(text, meta)
  local file = (meta and meta.file) or ""
  local label = file ~= "" and ("󰨞 " .. file) or "󰨞 Code"
  local raw = sanitize(text)
  local code_lines = vim.split(raw, "\n", { plain = true })
  local is_diff = is_diff_content(raw)

  local max_lines = 20
  local body = {}
  local truncated = false
  for i, l in ipairs(code_lines) do
    if i > max_lines then truncated = true; break end
    table.insert(body, l)
  end
  if truncated then
    table.insert(body, "… (" .. (#code_lines - max_lines) .. " more lines)")
  end

  local card = build_card(label, body, 0)
  local start = buf_append(card)
  if start then
    hl_range(start, 1, "SGCardCode")
    hl_range(start + #card - 1, 1, "SGCardBorder")
    for i = 2, #card - 1 do
      local line_text = card[i] or ""
      local line_idx = start + i - 1
      local content = line_text:sub(5) -- strip "│ " prefix (may be multi-byte)
      if is_diff then
        if content:sub(1, 1) == "+" and content:sub(1, 3) ~= "+++" then
          hl_partial(line_idx, 0, 4, "SGCardBorder")
          hl_partial(line_idx, 4, -1, "SGDiffAdd")
        elseif content:sub(1, 1) == "-" and content:sub(1, 3) ~= "---" then
          hl_partial(line_idx, 0, 4, "SGCardBorder")
          hl_partial(line_idx, 4, -1, "SGDiffDel")
        elseif content:sub(1, 2) == "@@" then
          hl_partial(line_idx, 0, 4, "SGCardBorder")
          hl_partial(line_idx, 4, -1, "SGDiffHdr")
        else
          hl_partial(line_idx, 0, 4, "SGCardBorder")
          hl_partial(line_idx, 4, -1, "SGCardBody")
        end
      else
        hl_partial(line_idx, 0, 4, "SGCardBorder")
        hl_partial(line_idx, 4, -1, "SGCardBody")
      end
    end
  end
  buf_append({ "" })
  scroll_bottom()
end

--- Check if a line looks like a success message.
local function is_success_line(text)
  local t = text:lower()
  return t:find("successfully") ~= nil
      or t:find("^success") ~= nil
      or t:find("applied:") ~= nil
      or t:find("staged:") ~= nil
      or t:find("restored:") ~= nil
end

local function render_observation(text, meta)
  local file = (meta and meta.file) or ""
  local label = file ~= "" and ("󱍬 " .. file) or "󱍬 Result"
  local raw = sanitize(text)
  local body_lines = vim.split(raw, "\n", { plain = true })
  local is_diff = is_diff_content(raw)
  local card = build_card(label, body_lines, 30)
  local start = buf_append(card)
  if start then
    hl_range(start, 1, "SGCardResult")
    for i = 2, #card do
      local line_idx = start + i - 1
      local line_text = card[i] or ""
      local content = line_text:sub(5) -- strip "│ " prefix (may be multi-byte)
      hl_partial(line_idx, 0, 4, "SGCardBorder")
      if i < #card and #line_text > 4 then
        if is_diff then
          if content:sub(1, 1) == "+" and content:sub(1, 3) ~= "+++" then
            hl_partial(line_idx, 4, -1, "SGDiffAdd")
          elseif content:sub(1, 1) == "-" and content:sub(1, 3) ~= "---" then
            hl_partial(line_idx, 4, -1, "SGDiffDel")
          elseif content:sub(1, 2) == "@@" then
            hl_partial(line_idx, 4, -1, "SGDiffHdr")
          else
            hl_partial(line_idx, 4, -1, "SGCardBody")
          end
        elseif is_success_line(content) then
          hl_partial(line_idx, 4, -1, "SGSuccess")
        else
          hl_partial(line_idx, 4, -1, "SGCardBody")
        end
      else
        hl_range(line_idx, 1, "SGCardBorder")
      end
    end
  end
  buf_append({ "" })
  scroll_bottom()
end

local function render_error(text)
  local line = "󰅚 Error: " .. compact(text, 200)
  local start = buf_append({ line, "" })
  if start then hl_range(start, 1, "SGError") end
  scroll_bottom()
end

--- Render an inline approval prompt with a/r keys.
--- @param meta table  must contain tool and reply_fn
local function render_approval_prompt(meta)
  local tool = meta.tool or "?"
  local reply_fn = meta.reply_fn

  local prompt_line = "  [a] approve   [r] reject   (" .. tool .. ")"
  local start = buf_append({ prompt_line, "" })
  if start then
    hl_range(start, 1, "SGApproval")
  end
  scroll_bottom()

  -- Set temporary buffer keymaps for a/r
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  local bufnr = M.chat.bufnr

  local function cleanup_maps()
    pcall(vim.keymap.del, "n", "a", { buffer = bufnr })
    pcall(vim.keymap.del, "n", "r", { buffer = bufnr })
  end

  local responded = false

  vim.keymap.set("n", "a", function()
    if responded then return end
    responded = true
    cleanup_maps()
    -- Replace the prompt line with approved status
    if start and buf_valid(bufnr) then
      pcall(vim.api.nvim_buf_set_lines, bufnr, start, start + 1, false, { "  ✓ Approved" })
      hl_range(start, 1, "SGSuccess")
    end
    if reply_fn then reply_fn({ cmd = "approval_response", approved = true }) end
    M.set_thinking(true)
  end, { buffer = bufnr, silent = true, noremap = true, desc = "SG: approve" })

  vim.keymap.set("n", "r", function()
    if responded then return end
    responded = true
    cleanup_maps()
    if start and buf_valid(bufnr) then
      pcall(vim.api.nvim_buf_set_lines, bufnr, start, start + 1, false, { "  ✗ Rejected" })
      hl_range(start, 1, "SGError")
    end
    if reply_fn then reply_fn({ cmd = "approval_response", approved = false }) end
    M.set_thinking(true)
  end, { buffer = bufnr, silent = true, noremap = true, desc = "SG: reject" })

  -- Focus the chat window so user can press a/r immediately
  if M.chat and win_valid(M.chat.winid) then
    pcall(vim.api.nvim_set_current_win, M.chat.winid)
  end
end

-- ── NUI management ─────────────────────────────────────────────────────

local function set_prompt_top(text)
  if not M.prompt or not M.prompt.border then return end
  pcall(function() M.prompt.border:set_text("top", text, "center") end)
end

local function notify_err(msg)
  vim.schedule(function()
    vim.notify("ShellGeist: " .. tostring(msg), vim.log.levels.ERROR, { title = "ShellGeist" })
  end)
end

local function ensure_nui()
  if Layout and Input and Popup then return true end
  local ok1, m1 = pcall(require, "nui.layout")
  local ok2, m2 = pcall(require, "nui.input")
  local ok3, m3 = pcall(require, "nui.popup")
  if not (ok1 and ok2 and ok3) then
    notify_err("missing nui dependency")
    return false
  end
  Layout, Input, Popup = m1, m2, m3
  return true
end

local function reset_state()
  if spinner_timer then spinner_timer:stop(); spinner_timer:close(); spinner_timer = nil end
  M.layout = nil
  M.chat = nil
  M.prompt = nil
  M._streaming_assistant = false
  M._current_status = "request"
end

-- ── prompt submission ──────────────────────────────────────────────────

local function dispatch_sgagent(value)
  local goal = sanitize(value):gsub("\n", " "):gsub("^%s+", ""):gsub("%s+$", "")
  if goal == "" then return end

  local ok_mod, mod = pcall(require, "shellgeist")
  if ok_mod and mod and type(mod.run_agent) == "function" then
    local ok_run, run_err = pcall(mod.run_agent, goal)
    if not ok_run then notify_err("failed to submit: " .. tostring(run_err)) end
    return
  end

  local ok_cmd, cmd_err = pcall(function()
    vim.api.nvim_cmd({ cmd = "SGAgent", args = { goal } }, {})
  end)
  if not ok_cmd then notify_err("failed to submit: " .. tostring(cmd_err)) end
end

local function submit_from_prompt(prompt_widget)
  if not prompt_widget or not buf_valid(prompt_widget.bufnr) then return end
  local ok_lines, lines = pcall(vim.api.nvim_buf_get_lines, prompt_widget.bufnr, 0, -1, false)
  if not ok_lines then return end

  local value = sanitize(table.concat(lines or {}, "\n"))
  if value ~= "" then
    vim.schedule(function() dispatch_sgagent(value) end)
  end

  pcall(vim.api.nvim_buf_set_lines, prompt_widget.bufnr, 0, -1, false, { "" })
  if win_valid(prompt_widget.winid) then
    pcall(vim.api.nvim_set_current_win, prompt_widget.winid)
    pcall(vim.cmd, "startinsert")
  end
end

-- ── public API ─────────────────────────────────────────────────────────

function M.is_open()
  return M.layout and M.layout.winid and win_valid(M.layout.winid)
end

function M.toggle()
  if M.is_open() then
    pcall(function() M.layout:unmount() end)
    reset_state()
  else
    M.open()
  end
end

function M.open()
  if M.is_open() then return end
  if not ensure_nui() then return end

  local ok, err = pcall(function()
    define_highlights()

    local chat = Popup({
      enter = false,
      border = {
        style = "rounded",
        highlight = "SGBorder",
        text = { top = "  ShellGeist ", top_align = "center" },
      },
      buf_options = { filetype = "shellgeist", buftype = "nofile" },
      win_options = {
        wrap = true,
        linebreak = true,
        winhighlight = "Normal:SGBody,FloatBorder:SGBorder",
      },
    })

    local prompt_widget
    local sg = require("shellgeist")
    local mode = sg.get_mode and sg.get_mode() or "auto"
    local prompt_label = mode == "review" and " [Review] " or " [Request] "
    prompt_widget = Input({
      border = {
        style = "rounded",
        highlight = "SGBorder",
        text = { top = prompt_label, top_align = "center" },
      },
      win_options = {
        winhighlight = "Normal:Normal,FloatBorder:SGBorder",
      },
    }, {
      on_submit = function() end, -- no-op: overridden after mount
    })

    M.chat = chat
    M.prompt = prompt_widget
    M._current_status = "request"

    M.layout = Layout(
      {
        relative = "editor",
        position = { row = 0, col = "100%" },
        size = { width = 50, height = "100%" },
      },
      Layout.Box({
        Layout.Box(M.chat, { size = "88%" }),
        Layout.Box(M.prompt, { size = "12%" }),
      }, { dir = "col" })
    )

    M.layout:mount()
    M.render_welcome()

    -- Override <CR> AFTER mount to replace NUI's close-on-submit.
    prompt_widget:map("i", "<CR>", function() submit_from_prompt(prompt_widget) end, { noremap = true, nowait = true })
    prompt_widget:map("n", "<CR>", function() submit_from_prompt(prompt_widget) end, { noremap = true, nowait = true })

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
    " ShellGeist",
    "",
    "Ready to assist you.",
    "───────────────────────────────────────────",
    "",
  }
  pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, 0, -1, false, lines)
  pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "SGTitle", 0, 0, -1)
  pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "SGCardBorder", 3, 0, -1)
end

function M.focus_prompt()
  if M.prompt and win_valid(M.prompt.winid) then
    vim.schedule(function()
      pcall(vim.api.nvim_set_current_win, M.prompt.winid)
      vim.cmd("startinsert")
    end)
  end
end

-- ── append_text (main entry for all message types) ─────────────────────

function M.append_text(text, msg_type, meta)
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  text = sanitize(text)
  meta = type(meta) == "table" and meta or {}

  -- ── streaming response chunks ──
  if msg_type == "assistant_chunk" or msg_type == "response_chunk" then
    local chunk = text
    if chunk == "" then return end
    local is_first = not M._streaming_assistant
    if is_first then
      M._streaming_assistant = true
      local hdr = buf_append({ "󰚩 Assistant" })
      if hdr then hl_range(hdr, 1, "SGResponse") end
      buf_append({ "" })  -- content starts on new line
    end
    local ok_count, line_count = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
    if not ok_count then return end
    local last_idx = line_count - 1
    local ok_last, last_lines = pcall(vim.api.nvim_buf_get_lines, M.chat.bufnr, last_idx, last_idx + 1, false)
    if not ok_last then return end
    local last_line = (last_lines and last_lines[1]) or ""
    local nl = chunk:find("\n", 1, true)
    if nl then
      pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_idx, last_idx + 1, false, { last_line .. chunk:sub(1, nl - 1) })
      hl_range(last_idx, 1, "SGResponseBody")
      local new_lines = vim.split(chunk:sub(nl + 1), "\n", { plain = true })
      local new_start = buf_append(new_lines)
      if new_start then hl_range(new_start, #new_lines, "SGResponseBody") end
    else
      pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_idx, last_idx + 1, false, { last_line .. chunk })
      hl_range(last_idx, 1, "SGResponseBody")
    end
    scroll_bottom()
    return
  end

  -- Any non-chunk type ends streaming
  M._streaming_assistant = false

  -- ── status/info → prompt border only ──
  if msg_type == "info" then
    set_prompt_top(string.format(" 󰋽 %s ", text:gsub("\n", " "):sub(1, 40)))
    return
  end

  -- ── dispatch to type-specific renderers ──
  if msg_type == "user" then
    render_user(text)
  elseif msg_type == "assistant" or msg_type == "response" then
    render_response(text)
  elseif msg_type == "thinking" or msg_type == "thought" then
    render_thinking(text)
  elseif msg_type == "action" then
    render_action(text, meta)
  elseif msg_type == "code" then
    render_code(text, meta)
  elseif msg_type == "observation" then
    render_observation(text, meta)
  elseif msg_type == "error" then
    render_error(text)
  elseif msg_type == "approval_prompt" then
    render_approval_prompt(meta)
  end
end

-- ── thinking spinner ───────────────────────────────────────────────────

function M.set_thinking(is_thinking)
  if not M.prompt or not M.prompt.border then return end

  if is_thinking then
    if spinner_timer then return end
    local frame = 1
    spinner_timer = vim.loop.new_timer()
    spinner_timer:start(0, 100, vim.schedule_wrap(function()
      if not M.prompt or not M.prompt.border then
        if spinner_timer then spinner_timer:stop(); spinner_timer:close(); spinner_timer = nil end
        return
      end
      if M._current_status == "request" or M._current_status == "thinking" then
        M._current_status = "thinking"
        set_prompt_top(" Thinking " .. spinner_frames[frame] .. " ")
      end
      frame = (frame % #spinner_frames) + 1
    end))
  else
    if spinner_timer then spinner_timer:stop(); spinner_timer:close(); spinner_timer = nil end
    M._current_status = "request"
    local sg = require("shellgeist")
    local mode = sg.get_mode and sg.get_mode() or "auto"
    local label = mode == "review" and " [Review] " or " [Request] "
    set_prompt_top(label)
  end
end

return M
