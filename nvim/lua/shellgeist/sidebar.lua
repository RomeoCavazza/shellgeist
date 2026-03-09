-- ╔═══════════════════════════════════════════════════════════════════════╗
-- ║  ShellGeist sidebar — avante-inspired chat UI                       ║
-- ╚═══════════════════════════════════════════════════════════════════════╝
local M = {}

M.layout = nil
M.chat = nil
M.prompt = nil
M._streaming_assistant = false
M._streaming_thinking = false
M._current_status = "request"

local chat_ns = vim.api.nvim_create_namespace("shellgeist_chat")
local spinner_frames = { "⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏" }
-- Nerd Font robot (Material Design Icons robot, U+F06A9)
local _assistant_header = string.char(0xF3, 0xB0, 0x9A, 0xA9) .. " Assistant"
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

-- ── highlight definitions ──────────────────────────────────────────────

local function define_highlights()
  local hl = vim.api.nvim_set_hl
  -- Chrome
  hl(0, "SGBorder",       { fg = "#3b82f6", ctermfg = 69 })
  hl(0, "SGTitle",        { fg = "#1e222a", bg = "#98c379", ctermfg = 235, ctermbg = 114, bold = true })
  -- User: header "󰀄 User" only in blue; body stays default
  hl(0, "SGUser",         { fg = "#61afef", ctermfg = 75, bold = true })
  hl(0, "SGUserBody",     { fg = "#e0e0e0", ctermfg = 253 })
  -- Response / Assistant: header uses Nerd Font robot icon; body stays default
  hl(0, "SGResponse",     { fg = "#7f848e", ctermfg = 243, bold = true })
  hl(0, "SGResponseBody", { fg = "#e0e0e0", ctermfg = 253 })
  -- Thinking
  hl(0, "SGThinking",     { fg = "#7f848e", ctermfg = 243, italic = true })
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
  end, 50)
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

--- Check if text contains conflict markers (<<<<, =====, >>>>>).
local function is_conflict_content(text)
  return text and (text:find("<<<<<<<") or text:find("=======") or text:find(">>>>>>>"))
end

--- Extract the unified diff portion from a write_file-style observation ("Successfully wrote...\n\nDiff:\n--- a/...").
--- Returns the full text if no clear diff block is found.
local function extract_diff_from_observation(text)
  if not text or text == "" then return text end
  local from = text:find("\n--- a/") or text:find("\n@@")
  if from then
    return text:sub(from + 1)  -- skip the leading \n
  end
  if text:find("^--- a/") or text:find("^@@") then
    return text
  end
  return text
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

--- Returns true if s contains only <tool_use>...</tool_use> and whitespace (no visible prose).
local function content_is_only_tool_use(s)
  if not s or s == "" then return true end
  local stripped = s:gsub("<tool_use>[%s%S]-</tool_use>", ""):gsub("%s+", " "):gsub("^%s*", ""):gsub("%s*$", "")
  return stripped == ""
end

--- Strip Status: DONE / Status: FAILED lines and trailing status from text (hidden in UI).
local function strip_status_lines(s)
  if not s or s == "" then return s end
  local lines = vim.split(s, "\n", { plain = true })
  local out = {}
  for _, ln in ipairs(lines) do
    local t = vim.trim(ln)
    if t ~= "" and not t:match("^Status:%s*(DONE|FAILED)") then
      table.insert(out, ln)
    end
  end
  s = table.concat(out, "\n")
  -- Remove trailing " Status: DONE" or " Status: FAILED: ..." from last line
  s = s:gsub("%s*Status:%s*DONE%s*$", "")
  s = s:gsub("%s*Status:%s*FAILED[^\n]*$", "")
  return vim.trim(s)
end

local function render_response(text)
  -- Strip any "Thought:" prefix that was already emitted separately
  local body = sanitize(text)
  body = body:gsub("^%s*Thoughts?:%s*.-\n\n", "")
  body = strip_status_lines(body)
  if body == "" then return end

  local header = _assistant_header
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
  local first = "... " .. (lines[1] or "")
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
  if file ~= "" then label = label .. " " .. file end
  -- Compact inline: single dimmed line
  local desc = compact(text, 120)
  desc = desc:gsub("^Calling:%s*", "")
  local line = "  → " .. label
  if desc ~= "" and desc ~= tool then line = line .. "  " .. desc end
  local start = buf_append({ line })
  if start then hl_range(start, 1, "SGThinking") end
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

-- Strip ANSI escape sequences so run_shell output (e.g. cube animation) doesn't break layout
local function strip_ansi(text)
  if not text or text == "" then return text end
  return (text:gsub("\27%[[%d;]*[a-zA-Z]", ""):gsub("\27%[%?%d;]*[a-zA-Z]", ""):gsub("\27%[=%%]?[^a-zA-Z]*[a-zA-Z]", ""))
end

local function render_observation(text, meta)
  local raw = sanitize(text)
  raw = strip_ansi(raw)
  local is_diff = is_diff_content(raw)
  -- Explicit success/failure from backend takes precedence over content heuristics
  local meta_success = (meta and meta.success ~= nil) and meta.success
  local is_err = (meta_success == false)
      or raw:lower():find("^error") ~= nil
      or raw:lower():find("validation failed") ~= nil
      or raw:lower():find("directory not found") ~= nil
      or raw:lower():find("file not found") ~= nil
      or raw:lower():find("access denied") ~= nil
      or raw:lower():find("blocked_") ~= nil
  local is_succ = (meta_success == true) or (meta_success ~= false and is_success_line(raw))

  -- For diffs: show a clear "Diff" card (---/+++/@@) with green/red; box height = full diff (no cap)
  if is_diff then
    local body_raw = extract_diff_from_observation(raw)
    local body_lines = vim.split(body_raw, "\n", { plain = true })
    local card = build_card("Diff", body_lines, 0)
    local start = buf_append(card)
    if start then
      hl_range(start, 1, "SGCardResult")
      hl_range(start + #card - 1, 1, "SGCardBorder")
      for i = 2, #card - 1 do
        local line_idx = start + i - 1
        local line_text = card[i] or ""
        local content = line_text:sub(5)
        hl_partial(line_idx, 0, 4, "SGCardBorder")
        if content:sub(1, 7) == "<<<<<<<" then
          hl_partial(line_idx, 4, -1, "SGDiffAdd")
        elseif content:sub(1, 7) == "=======" then
          hl_partial(line_idx, 4, -1, "SGDiffHdr")
        elseif content:sub(1, 7) == ">>>>>>>" then
          hl_partial(line_idx, 4, -1, "SGDiffDel")
        elseif content:sub(1, 1) == "+" and content:sub(1, 3) ~= "+++" then
          hl_partial(line_idx, 4, -1, "SGDiffAdd")
        elseif content:sub(1, 1) == "-" and content:sub(1, 3) ~= "---" then
          hl_partial(line_idx, 4, -1, "SGDiffDel")
        elseif content:sub(1, 2) == "@@" then
          hl_partial(line_idx, 4, -1, "SGDiffHdr")
        else
          hl_partial(line_idx, 4, -1, "SGCardBody")
        end
      end
    end
    buf_append({ "" })
    scroll_bottom()
    return
  end

  -- Conflict markers (<<<</====/>>>>) without unified diff: show as "Conflict" card (full height)
  if is_conflict_content(raw) then
    local body_lines = vim.split(raw, "\n", { plain = true })
    local card = build_card("Conflict", body_lines, 0)
    local start = buf_append(card)
    if start then
      hl_range(start, 1, "SGCardResult")
      hl_range(start + #card - 1, 1, "SGCardBorder")
      for i = 2, #card - 1 do
        local line_idx = start + i - 1
        local line_text = card[i] or ""
        local content = line_text:sub(5)
        hl_partial(line_idx, 0, 4, "SGCardBorder")
        if content:sub(1, 7) == "<<<<<<<" then
          hl_partial(line_idx, 4, -1, "SGDiffAdd")
        elseif content:sub(1, 7) == "=======" then
          hl_partial(line_idx, 4, -1, "SGDiffHdr")
        elseif content:sub(1, 7) == ">>>>>>>" then
          hl_partial(line_idx, 4, -1, "SGDiffDel")
        else
          hl_partial(line_idx, 4, -1, "SGCardBody")
        end
      end
    end
    buf_append({ "" })
    scroll_bottom()
    return
  end

  -- Long run_shell-style output: show in a fixed-height card so it doesn't explode the sidebar
  local lines = vim.split(raw, "\n", { plain = true })
  local max_run_output_lines = 14
  if #lines > 3 and not is_diff then
    local visible = vim.list_slice(lines, 1, max_run_output_lines)
    local truncated = #lines > max_run_output_lines
    local card = build_card("Output", visible, 0)
    if truncated then
      table.insert(card, #card, "│ … (" .. (#lines - max_run_output_lines) .. " more lines)")
    end
    local start = buf_append(card)
    if start then
      hl_range(start, 1, "SGCardResult")
      hl_range(start + #card - 1, 1, "SGCardBorder")
      for i = 2, #card - 1 do
        hl_range(start + i - 1, 1, "SGCardBody")
      end
    end
    buf_append({ "" })
    scroll_bottom()
    return
  end

  -- For short results: compact inline
  local summary = compact(raw, 160)
  local prefix, hl_group
  if is_err then
    prefix = "  - "
    hl_group = "SGError"
  elseif is_succ then
    prefix = "  + "
    hl_group = "SGSuccess"
  else
    prefix = "  ← "
    hl_group = "SGCardBody"
  end
  local line = prefix .. summary
  local start = buf_append({ line })
  if start then hl_range(start, 1, hl_group) end
  scroll_bottom()
end

local function render_error(text)
  local line = "Error: " .. compact(text, 200)
  local start = buf_append({ line, "" })
  if start then hl_range(start, 1, "SGError") end
  scroll_bottom()
end

--- Render an in-sidebar diff card with [a] accept / [r] reject.
--- Used for review_pending (hunk-level review for edit_file in review mode)
--- and file_changed (auto mode, post-write).   Like claude-code's terminal.
--- @param meta table  requires file, old_content, new_content, reply_fn
local function render_diff_review(meta)
  local file = meta.file or "?"
  local old_content = meta.old_content or ""
  local new_content = meta.new_content or ""
  local reply_fn = meta.reply_fn

  -- Compute unified diff
  local ok_d, diff_text = pcall(vim.diff, old_content, new_content, {
    algorithm = "histogram",
    result_type = "unified",
    ctxlen = 3,
  })

  if not ok_d or not diff_text or diff_text == "" then
    -- No differences → auto-approve
    local s = buf_append({ "  No changes for " .. file, "" })
    if s then hl_range(s, 1, "SGCardBody") end
    scroll_bottom()
    if reply_fn then reply_fn({ cmd = "review_decision", approved = true, content = new_content }) end
    M.set_thinking(true)
    return
  end

  -- Parse diff lines, skip --- / +++ headers (file is in the card title)
  local raw_lines = vim.split(diff_text, "\n", { plain = true })
  local body = {}
  local max_lines = 50
  local truncated = false
  for _, l in ipairs(raw_lines) do
    if not l:match("^%-%-%-") and not l:match("^%+%+%+") then
      if #body >= max_lines then truncated = true; break end
      body[#body + 1] = l
    end
  end
  if truncated then
    body[#body + 1] = "… (" .. (#raw_lines - max_lines) .. " more lines)"
  end

  -- Build card
  local label = "File " .. file
  local card = build_card(label, body, 0)
  local start = buf_append(card)
  if start then
    hl_range(start, 1, "SGCardCode")
    hl_range(start + #card - 1, 1, "SGCardBorder")
    for i = 2, #card - 1 do
      local line_text = card[i] or ""
      local line_idx = start + i - 1
      local c = line_text:sub(5)  -- strip "│ " (3-byte char + space)
      hl_partial(line_idx, 0, 4, "SGCardBorder")
      if c:sub(1, 1) == "+" and c:sub(1, 3) ~= "+++" then
        hl_partial(line_idx, 4, -1, "SGDiffAdd")
      elseif c:sub(1, 1) == "-" and c:sub(1, 3) ~= "---" then
        hl_partial(line_idx, 4, -1, "SGDiffDel")
      elseif c:sub(1, 2) == "@@" then
        hl_partial(line_idx, 4, -1, "SGDiffHdr")
      else
        hl_partial(line_idx, 4, -1, "SGCardBody")
      end
    end
  end

  -- Accept / reject prompt
  local prompt_line = "  [o] open   [a] accept   [r] reject   (" .. file .. ")"
  local prompt_start = buf_append({ prompt_line, "" })
  if prompt_start then
    hl_range(prompt_start, 1, "SGApproval")
  end
  scroll_bottom()

  -- Keybindings
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  local bufnr = M.chat.bufnr
  local responded = false
  local opened_filepath = nil  -- set when [o] is pressed, so [a] can read from that buffer

  local function cleanup()
    pcall(vim.keymap.del, "n", "o", { buffer = bufnr })
    pcall(vim.keymap.del, "n", "a", { buffer = bufnr })
    pcall(vim.keymap.del, "n", "r", { buffer = bufnr })
  end

  local function cleanup_o_only()
    pcall(vim.keymap.del, "n", "o", { buffer = bufnr })
  end

  -- [o] open: open file with conflict markers; keep [a]/[r] so user can accept/reject from sidebar too
  vim.keymap.set("n", "o", function()
    if responded then return end
    cleanup_o_only()
    if prompt_start and buf_valid(bufnr) then
      pcall(vim.api.nvim_buf_set_lines, bufnr, prompt_start, prompt_start + 1, false, { "  Open in editor — [a] accept   [r] reject   (" .. file .. ")" })
      hl_range(prompt_start, 1, "SGApproval")
    end
    -- Resolve absolute file path and store for [a] (read buffer content later)
    local filepath = file
    local file_root = meta.root or ""
    if file_root ~= "" and not filepath:match("^/") then
      filepath = file_root .. "/" .. filepath
    end
    opened_filepath = filepath
    -- Switch to a normal (non-floating) editor window before opening
    local sidebar_wins = {}
    if M.chat and win_valid(M.chat.winid) then sidebar_wins[M.chat.winid] = true end
    if M.prompt and win_valid(M.prompt.winid) then sidebar_wins[M.prompt.winid] = true end
    local target_win = nil
    for _, w in ipairs(vim.api.nvim_list_wins()) do
      if not sidebar_wins[w] and vim.api.nvim_win_get_config(w).relative == "" then
        target_win = w
        break
      end
    end
    if target_win then
      vim.api.nvim_set_current_win(target_win)
    end
    -- Open with conflict markers via conflict.lua
    local conflict = require("shellgeist.conflict")
    conflict.show_inline(filepath, old_content, new_content, {
      on_complete = function(resolved_content)
        -- When user finishes in conflict buffer (ct/ca/cr), we still cleanup sidebar keys and reply
        responded = true
        cleanup()
        if prompt_start and buf_valid(bufnr) then
          pcall(vim.api.nvim_buf_set_lines, bufnr, prompt_start, prompt_start + 1, false, { "  + Resolved in editor" })
          hl_range(prompt_start, 1, "SGSuccess")
        end
        if resolved_content then
          if reply_fn then reply_fn({ cmd = "review_decision", approved = true, content = resolved_content }) end
        else
          if reply_fn then reply_fn({ cmd = "review_decision", approved = false }) end
        end
        M.set_thinking(true)
      end,
    })
    vim.notify("ShellGeist: In editor: cr=reject  ct/ca=accept. Or in sidebar: [a] accept  [r] reject", vim.log.levels.INFO, { title = "ShellGeist" })
  end, { buffer = bufnr, silent = true, noremap = true, desc = "SG: open in editor" })

  vim.keymap.set("n", "a", function()
    if responded then return end
    responded = true
    cleanup()
    if prompt_start and buf_valid(bufnr) then
      pcall(vim.api.nvim_buf_set_lines, bufnr, prompt_start, prompt_start + 1, false, { "  + Accepted" })
      hl_range(prompt_start, 1, "SGSuccess")
    end
    local content_to_send = new_content
    if opened_filepath and vim.api.nvim_buf_is_valid(vim.fn.bufnr(opened_filepath)) then
      local ok, lines = pcall(vim.api.nvim_buf_get_lines, vim.fn.bufnr(opened_filepath), 0, -1, false)
      if ok and lines and #lines > 0 then
        content_to_send = table.concat(lines, "\n")
        if not content_to_send:match("\n$") then content_to_send = content_to_send .. "\n" end
      end
    end
    if reply_fn then reply_fn({ cmd = "review_decision", approved = true, content = content_to_send }) end
    M.set_thinking(true)
  end, { buffer = bufnr, silent = true, noremap = true, desc = "SG: accept diff" })

  vim.keymap.set("n", "r", function()
    if responded then return end
    responded = true
    cleanup()
    if prompt_start and buf_valid(bufnr) then
      pcall(vim.api.nvim_buf_set_lines, bufnr, prompt_start, prompt_start + 1, false, { "  - Rejected" })
      hl_range(prompt_start, 1, "SGError")
    end
    if reply_fn then reply_fn({ cmd = "review_decision", approved = false }) end
    M.set_thinking(true)
  end, { buffer = bufnr, silent = true, noremap = true, desc = "SG: reject diff" })

  -- Focus chat window so user can press a/r
  if M.chat and win_valid(M.chat.winid) then
    pcall(vim.api.nvim_set_current_win, M.chat.winid)
  end
end

--- Render an inline approval prompt with a/r keys.
--- @param meta table  must contain tool and reply_fn
local function render_approval_prompt(meta)
  local tool = meta.tool or "?"
  local reply_fn = meta.reply_fn
  local is_write = (tool == "write_file")

  local prompt_line = "  [a] approve   [r] reject   (" .. tool .. ")"
  if is_write then
    prompt_line = "  [o] open   [a] approve   [r] reject   (" .. tool .. ")"
  end

  local start = buf_append({ prompt_line, "" })
  if start then
    hl_range(start, 1, "SGApproval")
  end
  scroll_bottom()

  -- Set temporary buffer keymaps for a/r
  if not M.chat or not buf_valid(M.chat.bufnr) then return end
  local bufnr = M.chat.bufnr

  local responded = false

  local function cleanup_maps()
    pcall(vim.keymap.del, "n", "a", { buffer = bufnr })
    pcall(vim.keymap.del, "n", "r", { buffer = bufnr })
    if is_write then
      pcall(vim.keymap.del, "n", "o", { buffer = bufnr })
    end
  end

  local function cleanup_o_only()
    if is_write then pcall(vim.keymap.del, "n", "o", { buffer = bufnr }) end
  end

  if is_write then
    vim.keymap.set("n", "o", function()
      if responded then return end
      cleanup_o_only()
      if start and buf_valid(bufnr) then
        pcall(vim.api.nvim_buf_set_lines, bufnr, start, start + 1, false, { "  Open in editor — [a] approve   [r] reject   (" .. (meta.tool or "") .. ")" })
        hl_range(start, 1, "SGApproval")
      end

      -- Extract file path and new content from tool args
      local args = meta.args or {}
      local file_rel = args.path or args.file_path or args.file or ""
      local new_content = args.content or ""
      local file_root = meta.root or ""

      local filepath = file_rel
      if file_root ~= "" and not filepath:match("^/") then
        filepath = file_root .. "/" .. filepath
      end

      -- Get current file content (old content)
      local old_content = ""
      if vim.fn.filereadable(filepath) == 1 then
        old_content = table.concat(vim.fn.readfile(filepath), "\n") .. "\n"
      end

      -- Switch to editor window
      local sidebar_wins = {}
      if M.chat and win_valid(M.chat.winid) then sidebar_wins[M.chat.winid] = true end
      if M.prompt and win_valid(M.prompt.winid) then sidebar_wins[M.prompt.winid] = true end
      local target_win = nil
      for _, w in ipairs(vim.api.nvim_list_wins()) do
        if not sidebar_wins[w] and vim.api.nvim_win_get_config(w).relative == "" then
          target_win = w
          break
        end
      end
      if target_win then vim.api.nvim_set_current_win(target_win) end

      -- Show conflict markers; keep [a]/[r] in sidebar so user can approve/reject from there too
      local conflict = require("shellgeist.conflict")
      conflict.show_inline(filepath, old_content, new_content, {
        on_complete = function(resolved_content)
          responded = true
          cleanup_maps()
          if start and buf_valid(bufnr) then
            pcall(vim.api.nvim_buf_set_lines, bufnr, start, start + 1, false, { "  + Resolved in editor" })
            hl_range(start, 1, "SGSuccess")
          end
          if resolved_content then
            if reply_fn then reply_fn({ cmd = "approval_response", approved = true }) end
          else
            if reply_fn then reply_fn({ cmd = "approval_response", approved = false }) end
          end
          M.set_thinking(true)
        end,
      })
      vim.notify("ShellGeist: In editor: cr=reject  ct/ca=accept. Or in sidebar: [a] approve  [r] reject", vim.log.levels.INFO, { title = "ShellGeist" })
    end, { buffer = bufnr, silent = true, noremap = true, desc = "SG: open for review" })
  end

  vim.keymap.set("n", "a", function()
    if responded then return end
    responded = true
    cleanup_maps()
    -- Replace the prompt line with approved status
    if start and buf_valid(bufnr) then
      pcall(vim.api.nvim_buf_set_lines, bufnr, start, start + 1, false, { "  + Approved" })
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
      pcall(vim.api.nvim_buf_set_lines, bufnr, start, start + 1, false, { "  - Rejected" })
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
  M._streaming_thinking = false
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
        text = { top = "  [Response] ", top_align = "center" },
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
    if type(sg.set_context_from_project) == "function" then
      sg.set_context_from_project()
    end
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
  M._streaming_thinking = false
  -- ASCII banner "SHELLGEIST" — scale to fit sidebar width (UTF-8 safe).
  -- Neovim has no per-window font size, so we shrink by keeping every 2nd character (~50% width).
  local function utf8_byte_len(b)
    if b == nil or b < 128 then return 1 end
    if b >= 0x80 and b < 0xC0 then return 1 end
    if b < 0xE0 then return 2 end
    if b < 0xF0 then return 3 end
    if b < 0xF8 then return 4 end
    return 1
  end
  local function shrink_line(s)
    local spans = {}
    local i = 1
    while i <= #s do
      local b = s:byte(i)
      local len = math.min(utf8_byte_len(b), #s - i + 1)
      spans[#spans + 1] = { start = i, ["end"] = i + len - 1 }
      i = i + len
    end
    if #spans < 2 then return s end
    local out = {}
    for j = 1, #spans, 2 do
      local sp = spans[j]
      out[#out + 1] = s:sub(sp.start, sp["end"])
    end
    return table.concat(out)
  end
  local banner = {
    "  ██████  ██░ ██ ▓█████  ██▓     ██▓      ▄████ ▓█████  ██▓  ██████ ▄▄▄█████▓",
    "▒██    ▒ ▓██░ ██▒▓█   ▀ ▓██▒    ▓██▒     ██▒ ▀█▒▓█   ▀ ▓██▒▒██    ▒ ▓  ██▒ ▓▒",
    "░ ▓██▄   ▒██▀▀██░▒███   ▒██░    ▒██░    ▒██░▄▄▄░▒███   ▒██▒░ ▓██▄   ▒ ▓██░ ▒░",
    "  ▒   ██▒░▓█ ░██ ▒▓█  ▄ ▒██░    ▒██░    ░▓█  ██▓▒▓█  ▄ ░██░  ▒   ██▒░ ▓██▓ ░ ",
    "▒██████▒▒░▓█▒░██▓░▒████▒░██████▒░██████▒░▒▓███▀▒░▒████▒░██░▒██████▒▒  ▒██▒ ░ ",
    "▒ ▒▓▒ ▒ ░ ▒ ░░▒░▒░░ ▒░ ░░ ▒░▓  ░░ ▒░▓  ░ ░▒   ▒ ░░ ▒░ ░░▓  ▒ ▒▓▒ ▒ ░  ▒ ░░   ",
    "░ ░▒  ░ ░ ▒ ░▒░ ░ ░ ░  ░░ ░ ▒  ░░ ░ ▒  ░  ░   ░  ░ ░  ░ ▒ ░░ ░▒  ░ ░    ░    ",
    "░  ░  ░   ░  ░░ ░   ░     ░ ░     ░ ░   ░ ░   ░    ░    ▒ ░░  ░  ░    ░      ",
    "      ░   ░  ░  ░   ░  ░    ░  ░    ░  ░      ░    ░  ░ ░        ░           ",
  }
  local sidebar_width = 50
  if M.chat and win_valid(M.chat.winid) then
    sidebar_width = vim.api.nvim_win_get_width(M.chat.winid)
  end
  local function center_line(s)
    local w = vim.fn.strdisplaywidth(s)
    local pad = math.max(0, math.floor((sidebar_width - w) / 2))
    return string.rep(" ", pad) .. s
  end
  local lines = { "" }
  for _, ln in ipairs(banner) do
    table.insert(lines, center_line(shrink_line(ln)))
  end
  table.insert(lines, "")
  table.insert(lines, "───────────────────────────────────────────")
  table.insert(lines, "  review: [a] accept  [r] reject  [o] open")
  table.insert(lines, "  modes:  :SGMode auto | review")
  table.insert(lines, "  nav:    q close  <Esc> → chat")

  -- Show last known workspace context (root / mode) for quick diagnostics; session hidden.
  local ok_sg, sg = pcall(require, "shellgeist")
  if ok_sg and sg and type(sg.get_last_context) == "function" then
    local ctx = sg.get_last_context() or {}
    local root = ctx.root or "(unknown root)"
    local mode = ctx.mode or "auto"
    table.insert(lines, string.format("  root:    %s", root))
    table.insert(lines, string.format("  mode:    %s", mode))
  end

  table.insert(lines, "───────────────────────────────────────────")
  table.insert(lines, "")
  pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, 0, -1, false, lines)
  -- Banner: SGThinking only (no SGTitle → avoids green inverse on first lines)
  for line_0 = 1, 9 do
    pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "SGThinking", line_0, 0, -1)
  end
  local sep2 = math.max(12, #lines - 1)
  for _, i in ipairs({ 11, 14, sep2 }) do
    pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "SGCardBorder", i, 0, -1)
  end
  for _, i in ipairs({ 12, 13, 14 }) do
    pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "SGThinking", i, 0, -1)
  end
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

  -- ── streaming thinking chunks ──
  if msg_type == "thinking_chunk" then
    local chunk = text
    if chunk == "" then return end
    local is_first = not M._streaming_thinking
    if is_first then
      M._streaming_thinking = true
      local hdr = buf_append({ "... Thinking" })
      if hdr then hl_range(hdr, 1, "SGThinking") end
      buf_append({ "" })
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
      hl_range(last_idx, 1, "SGThinking")
      local new_lines = vim.split(chunk:sub(nl + 1), "\n", { plain = true })
      local new_start = buf_append(new_lines)
      if new_start then hl_range(new_start, #new_lines, "SGThinking") end
    else
      pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_idx, last_idx + 1, false, { last_line .. chunk })
      hl_range(last_idx, 1, "SGThinking")
    end
    scroll_bottom()
    return
  end

  -- ── streaming response chunks ──
  if msg_type == "assistant_chunk" or msg_type == "response_chunk" then
    -- End thinking stream if active
    if M._streaming_thinking then
      M._streaming_thinking = false
      buf_append({ "" })
    end
    local chunk = text
    if chunk == "" then return end
    local is_first = not M._streaming_assistant
    if is_first then
      M._streaming_assistant = true
      local hdr = buf_append({ _assistant_header })
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
  if M._streaming_assistant then
    M._streaming_assistant = false
    -- If the streamed content was only <tool_use>...</tool_use>, remove that block (keep tools active but hidden)
    local ok, line_count = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
    if ok and line_count >= 1 then
      local ok_get, line_list = pcall(vim.api.nvim_buf_get_lines, M.chat.bufnr, 0, line_count, false)
      if ok_get and line_list and #line_list >= 1 then
        local last_assistant_idx = nil
        for i = line_count, 1, -1 do
          if (line_list[i] or ""):find(_assistant_header) then
            last_assistant_idx = i
            break
          end
        end
        if last_assistant_idx and last_assistant_idx < line_count then
          local content_lines = {}
          for j = last_assistant_idx + 1, line_count do
            table.insert(content_lines, line_list[j] or "")
          end
          local content = table.concat(content_lines, "\n")
          if content_is_only_tool_use(content) then
            pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_assistant_idx - 1, line_count, false, {})
            scroll_bottom()
            -- fall through to dispatch (do not add separator)
          else
            local sep = buf_append({ "───────────────────────────────────────────" })
            if sep then hl_range(sep, 1, "SGCardBorder") end
            buf_append({ "" })
          end
        else
          local sep = buf_append({ "───────────────────────────────────────────" })
          if sep then hl_range(sep, 1, "SGCardBorder") end
          buf_append({ "" })
        end
      else
        local sep = buf_append({ "───────────────────────────────────────────" })
        if sep then hl_range(sep, 1, "SGCardBorder") end
        buf_append({ "" })
      end
    else
      local sep = buf_append({ "───────────────────────────────────────────" })
      if sep then hl_range(sep, 1, "SGCardBorder") end
      buf_append({ "" })
    end
  end
  if M._streaming_thinking then
    M._streaming_thinking = false
    buf_append({ "" })
  end

  -- ── status/info → prompt border only ──
  if msg_type == "info" then
    set_prompt_top(string.format(" 󰋽 %s ", text:gsub("\n", " "):sub(1, 40)))
    return
  end

  -- ── dispatch to type-specific renderers ──
  if msg_type == "user" then
    render_user(text)
  elseif msg_type == "assistant" or msg_type == "response" then
    -- When this is the final response after streaming, replace the streamed block instead of adding a duplicate
    if meta.final and M.chat and buf_valid(M.chat.bufnr) then
      local ok, line_count = pcall(vim.api.nvim_buf_line_count, M.chat.bufnr)
      if ok and line_count >= 2 then
        local ok_get, line_list = pcall(vim.api.nvim_buf_get_lines, M.chat.bufnr, 0, line_count, false)
        if ok_get and line_list and #line_list >= 2 then
          local sep = "───────────────────────────────────────────"
          local last_sep_idx = nil
          local last_assistant_idx = nil
          for i = line_count, 1, -1 do
            local L = (line_list[i] or ""):gsub("^%s+", ""):gsub("%s+$", "")
            if L == sep then last_sep_idx = i end
            if (line_list[i] or ""):find(_assistant_header) and last_assistant_idx == nil then
              last_assistant_idx = i
            end
            if last_sep_idx and last_assistant_idx then break end
          end
          if last_assistant_idx and last_sep_idx and last_assistant_idx < last_sep_idx then
            local body = sanitize(text):gsub("^%s*Thoughts?:%s*.-\n\n", "")
            body = strip_status_lines(body)
            body = vim.trim(body)
            if body ~= "" and not content_is_only_tool_use(body) then
              local new_lines = { _assistant_header }
              for _, ln in ipairs(vim.split(body, "\n", { plain = true })) do
                table.insert(new_lines, ln)
              end
              pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_assistant_idx - 1, last_sep_idx - 1, false, new_lines)
              pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "SGResponse", last_assistant_idx - 1, 0, -1)
              for j = last_assistant_idx, last_assistant_idx + #new_lines - 2 do
                pcall(vim.api.nvim_buf_add_highlight, M.chat.bufnr, chat_ns, "SGResponseBody", j, 0, -1)
              end
              scroll_bottom()
              return
            end
            if body == "" or content_is_only_tool_use(body) then
              -- Hide tool-only block: remove assistant header + content + separator
              pcall(vim.api.nvim_buf_set_lines, M.chat.bufnr, last_assistant_idx - 1, last_sep_idx, false, {})
              scroll_bottom()
              return
            end
          end
        end
      end
    end
    if content_is_only_tool_use(strip_status_lines(sanitize(text))) then
      return
    end
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
  elseif msg_type == "diff_review" then
    render_diff_review(meta)
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
