-- ╔═══════════════════════════════════════════════════════════════════════╗
-- ║  ShellGeist conflict — avante-style inline accept / reject          ║
-- ║                                                                     ║
-- ║  Injects <<<<<<< / ======= / >>>>>>> markers into a source buffer  ║
-- ║  and lets the user resolve each hunk with keybindings:              ║
-- ║    co  = keep original (ours)                                       ║
-- ║    ct  = accept new (theirs)                                        ║
-- ║    cb  = keep both                                                  ║
-- ║    ca  = accept all new changes at once                             ║
-- ║    ]x  = jump to next conflict                                      ║
-- ║    [x  = jump to prev conflict                                      ║
-- ╚═══════════════════════════════════════════════════════════════════════╝
local api = vim.api
local M = {}

local NAMESPACE = api.nvim_create_namespace("shellgeist_conflict")
local HINT_NS   = api.nvim_create_namespace("shellgeist_conflict_hint")

local MARKER_START  = "<<<<<<< ORIGINAL"
local MARKER_MIDDLE = "======="
local MARKER_END    = ">>>>>>> SHELLGEIST"

-- Per-buffer callbacks for review completion
local _on_complete_cbs = {}  -- bufnr -> function(content: string|nil)

-- ── highlights ─────────────────────────────────────────────────────────

local hl_defined = false
local function define_highlights()
  if hl_defined then return end
  hl_defined = true
  local hl = api.nvim_set_hl
  hl(0, "SGConflictCurrent",       { bg = "#2e1a1a" })           -- old (ours) — red tint
  hl(0, "SGConflictCurrentLabel",  { bg = "#3d1f1f", bold = true })
  hl(0, "SGConflictIncoming",      { bg = "#1a2e2a" })           -- new (theirs) — green tint
  hl(0, "SGConflictIncomingLabel", { bg = "#1f3d2f", bold = true })
  hl(0, "SGConflictMiddle",        { fg = "#60a5fa", bold = true })
end

-- ── conflict position tracking ─────────────────────────────────────────

--- @class SGConflictPos
--- @field start   integer  0-based line of <<<<<<< marker
--- @field mid     integer  0-based line of ======= marker
--- @field finish  integer  0-based line of >>>>>>> marker

--- Scan buffer and return a list of conflict positions.
--- @param bufnr integer
--- @return SGConflictPos[]
local function scan(bufnr)
  local lines = api.nvim_buf_get_lines(bufnr, 0, -1, false)
  local positions = {}
  local cur = nil
  for i, line in ipairs(lines) do
    local lnum = i - 1
    if line:match("^<<<<<<< ") then
      cur = { start = lnum }
    elseif cur and line == MARKER_MIDDLE then
      cur.mid = lnum
    elseif cur and cur.mid and line:match("^>>>>>>> ") then
      cur.finish = lnum
      positions[#positions + 1] = cur
      cur = nil
    end
  end
  return positions
end

-- ── highlighting ───────────────────────────────────────────────────────

--- Apply extmark highlights for all conflict regions in the buffer.
--- @param bufnr integer
local function highlight_conflicts(bufnr)
  api.nvim_buf_clear_namespace(bufnr, NAMESPACE, 0, -1)
  api.nvim_buf_clear_namespace(bufnr, HINT_NS, 0, -1)

  local positions = scan(bufnr)
  if #positions == 0 then return end

  for _, pos in ipairs(positions) do
    -- <<<<<<< label
    api.nvim_buf_set_extmark(bufnr, NAMESPACE, pos.start, 0, {
      hl_group = "SGConflictCurrentLabel", hl_eol = true, end_row = pos.start + 1,
      priority = 200,
    })
    -- old content (ours)
    if pos.mid > pos.start + 1 then
      api.nvim_buf_set_extmark(bufnr, NAMESPACE, pos.start + 1, 0, {
        hl_group = "SGConflictCurrent", hl_eol = true, end_row = pos.mid,
        priority = 200,
      })
    end
    -- ======= separator
    api.nvim_buf_set_extmark(bufnr, NAMESPACE, pos.mid, 0, {
      hl_group = "SGConflictMiddle", hl_eol = true, end_row = pos.mid + 1,
      priority = 200,
    })
    -- new content (theirs)
    if pos.finish > pos.mid + 1 then
      api.nvim_buf_set_extmark(bufnr, NAMESPACE, pos.mid + 1, 0, {
        hl_group = "SGConflictIncoming", hl_eol = true, end_row = pos.finish,
        priority = 200,
      })
    end
    -- >>>>>>> label
    api.nvim_buf_set_extmark(bufnr, NAMESPACE, pos.finish, 0, {
      hl_group = "SGConflictIncomingLabel", hl_eol = true, end_row = pos.finish + 1,
      priority = 200,
    })
  end

  -- Inline hint on first conflict
  api.nvim_buf_set_extmark(bufnr, HINT_NS, positions[1].start, 0, {
    virt_text = {
      { " [co: original  ct: accept  cb: both  ca: accept all  cr: reject  ]x/[x: nav] ", "SGConflictMiddle" },
    },
    virt_text_pos = "right_align",
    priority = 200,
  })
end

-- ── resolution ─────────────────────────────────────────────────────────

--- Find the conflict position surrounding the cursor.
--- @param bufnr integer
--- @return SGConflictPos?
local function get_current_pos(bufnr)
  local cursor = api.nvim_win_get_cursor(0)[1] - 1  -- 0-based
  for _, pos in ipairs(scan(bufnr)) do
    if cursor >= pos.start and cursor <= pos.finish then
      return pos
    end
  end
  return nil
end

--- Replace a conflict region with the chosen lines.
--- @param bufnr integer
--- @param pos SGConflictPos
--- @param lines string[]
local function replace_conflict(bufnr, pos, lines)
  api.nvim_buf_set_lines(bufnr, pos.start, pos.finish + 1, false, lines)
  highlight_conflicts(bufnr)
  -- If no conflicts left, clean up keymaps
  if #scan(bufnr) == 0 then
    M.cleanup(bufnr)
  end
end

--- Keep original content (ours).
--- @param bufnr integer
function M.choose_ours(bufnr)
  local pos = get_current_pos(bufnr)
  if not pos then return end
  local lines = api.nvim_buf_get_lines(bufnr, pos.start + 1, pos.mid, false)
  replace_conflict(bufnr, pos, lines)
end

--- Accept new content (theirs).
--- @param bufnr integer
function M.choose_theirs(bufnr)
  local pos = get_current_pos(bufnr)
  if not pos then return end
  local lines = api.nvim_buf_get_lines(bufnr, pos.mid + 1, pos.finish, false)
  replace_conflict(bufnr, pos, lines)
end

--- Keep both (ours then theirs).
--- @param bufnr integer
function M.choose_both(bufnr)
  local pos = get_current_pos(bufnr)
  if not pos then return end
  local ours   = api.nvim_buf_get_lines(bufnr, pos.start + 1, pos.mid, false)
  local theirs = api.nvim_buf_get_lines(bufnr, pos.mid + 1, pos.finish, false)
  local merged = vim.list_extend(vim.list_extend({}, ours), theirs)
  replace_conflict(bufnr, pos, merged)
end

--- Accept all theirs at once.
--- @param bufnr integer
function M.choose_all_theirs(bufnr)
  -- Resolve from bottom to top so line numbers don't shift
  local positions = scan(bufnr)
  for i = #positions, 1, -1 do
    local pos = positions[i]
    local lines = api.nvim_buf_get_lines(bufnr, pos.mid + 1, pos.finish, false)
    api.nvim_buf_set_lines(bufnr, pos.start, pos.finish + 1, false, lines)
  end
  highlight_conflicts(bufnr)
  if #scan(bufnr) == 0 then
    M.cleanup(bufnr)
  end
end

--- Reject the entire review: restore old content and fire callback with nil.
--- @param bufnr integer
function M.reject_review(bufnr)
  if not api.nvim_buf_is_valid(bufnr) then return end
  local cb = _on_complete_cbs[bufnr]
  _on_complete_cbs[bufnr] = nil
  -- Clear conflict UI
  api.nvim_buf_clear_namespace(bufnr, NAMESPACE, 0, -1)
  api.nvim_buf_clear_namespace(bufnr, HINT_NS, 0, -1)
  if vim.b[bufnr].shellgeist_conflict_maps then
    for _, lhs in ipairs({ "co", "ct", "cb", "ca", "cr", "]x", "[x" }) do
      pcall(vim.keymap.del, "n", lhs, { buffer = bufnr })
    end
    vim.b[bufnr].shellgeist_conflict_maps = false
  end
  -- Reload file from disk to restore original content
  vim.cmd("edit!")
  vim.schedule(function()
    vim.notify("ShellGeist: review rejected", vim.log.levels.WARN, { title = "ShellGeist" })
  end)
  -- Signal rejection to the daemon
  if cb then
    vim.schedule(function() cb(nil) end)
  end
end

--- Jump to next conflict.
--- @param bufnr integer
function M.jump_next(bufnr)
  local cursor = api.nvim_win_get_cursor(0)[1] - 1
  for _, pos in ipairs(scan(bufnr)) do
    if pos.start > cursor then
      api.nvim_win_set_cursor(0, { pos.start + 1, 0 })
      vim.cmd("normal! zz")
      return
    end
  end
end

--- Jump to prev conflict.
--- @param bufnr integer
function M.jump_prev(bufnr)
  local cursor = api.nvim_win_get_cursor(0)[1] - 1
  local positions = scan(bufnr)
  for i = #positions, 1, -1 do
    if positions[i].start < cursor then
      api.nvim_win_set_cursor(0, { positions[i].start + 1, 0 })
      vim.cmd("normal! zz")
      return
    end
  end
end

-- ── keymaps ────────────────────────────────────────────────────────────

--- Set up buffer-local keymaps for conflict resolution.
--- @param bufnr integer
local function setup_keymaps(bufnr)
  if vim.b[bufnr].shellgeist_conflict_maps then return end
  vim.b[bufnr].shellgeist_conflict_maps = true

  local function map(lhs, fn, desc)
    vim.keymap.set("n", lhs, fn, { buffer = bufnr, silent = true, noremap = true, desc = "SG conflict: " .. desc })
  end

  map("co", function() M.choose_ours(bufnr) end,       "keep original")
  map("ct", function() M.choose_theirs(bufnr) end,     "accept new")
  map("cb", function() M.choose_both(bufnr) end,       "keep both")
  map("ca", function() M.choose_all_theirs(bufnr) end, "accept all new")
  map("cr", function() M.reject_review(bufnr) end,     "reject all (review)")
  map("]x", function() M.jump_next(bufnr) end,         "next conflict")
  map("[x", function() M.jump_prev(bufnr) end,         "prev conflict")
end

--- Remove conflict keymaps and highlights.
--- If an ``on_complete`` callback was registered (review mode), calls it
--- with the resolved buffer content.
--- @param bufnr integer
function M.cleanup(bufnr)
  if not api.nvim_buf_is_valid(bufnr) then return end

  -- Capture resolved content before clearing anything
  local cb = _on_complete_cbs[bufnr]
  _on_complete_cbs[bufnr] = nil

  if cb then
    -- Read the resolved buffer content and send it back
    local lines = api.nvim_buf_get_lines(bufnr, 0, -1, false)
    local content = table.concat(lines, "\n") .. "\n"
    vim.schedule(function()
      cb(content)
    end)
  end

  api.nvim_buf_clear_namespace(bufnr, NAMESPACE, 0, -1)
  api.nvim_buf_clear_namespace(bufnr, HINT_NS, 0, -1)
  if vim.b[bufnr].shellgeist_conflict_maps then
    for _, lhs in ipairs({ "co", "ct", "cb", "ca", "cr", "]x", "[x" }) do
      pcall(vim.keymap.del, "n", lhs, { buffer = bufnr })
    end
    vim.b[bufnr].shellgeist_conflict_maps = false
  end
  vim.schedule(function()
    vim.notify("ShellGeist: all conflicts resolved", vim.log.levels.INFO, { title = "ShellGeist" })
  end)
end

-- ── public API ─────────────────────────────────────────────────────────

--- Compute per-hunk diffs between old and new content using vim.diff().
--- Returns a list of hunks: { old_start, old_count, new_start, new_count }
--- @param old_text string  full old content as a single string
--- @param new_text string  full new content as a single string
--- @return table[]  list of { old_start: int, old_count: int, new_start: int, new_count: int }
local function minimize_diff(old_text, new_text)
  -- vim.diff with result_type="indices" returns a list of {old_start, old_count, new_start, new_count}
  local ok, indices = pcall(vim.diff, old_text, new_text, {
    algorithm = "histogram",
    result_type = "indices",
    ctxlen = 0,
  })
  if not ok or not indices then
    -- Fallback: treat entire content as one hunk
    local old_count = select(2, old_text:gsub("\n", "")) + (old_text ~= "" and 1 or 0)
    local new_count = select(2, new_text:gsub("\n", "")) + (new_text ~= "" and 1 or 0)
    return {{ old_start = 1, old_count = old_count, new_start = 1, new_count = new_count }}
  end

  local hunks = {}
  for _, idx in ipairs(indices) do
    hunks[#hunks + 1] = {
      old_start = idx[1],
      old_count = idx[2],
      new_start = idx[3],
      new_count = idx[4],
    }
  end
  return hunks
end

--- Insert per-hunk conflict markers into a buffer.
--- Works bottom-to-top so line offsets don't shift.
--- @param bufnr integer
--- @param hunks table[]  list from minimize_diff
--- @param old_lines string[]  original file lines
--- @param new_lines string[]  new file lines
local function insert_conflict_contents(bufnr, hunks, old_lines, new_lines)
  -- Sort hunks by old_start descending (bottom-to-top insertion)
  table.sort(hunks, function(a, b) return a.old_start > b.old_start end)

  for _, hunk in ipairs(hunks) do
    local old_start = hunk.old_start  -- 1-based
    local old_count = hunk.old_count
    local new_start = hunk.new_start  -- 1-based
    local new_count = hunk.new_count

    -- Extract the old and new lines for this hunk
    local old_hunk = {}
    for i = old_start, old_start + old_count - 1 do
      old_hunk[#old_hunk + 1] = old_lines[i] or ""
    end

    local new_hunk = {}
    for i = new_start, new_start + new_count - 1 do
      new_hunk[#new_hunk + 1] = new_lines[i] or ""
    end

    -- Build conflict block
    local block = { MARKER_START }
    for _, l in ipairs(old_hunk) do block[#block + 1] = l end
    block[#block + 1] = MARKER_MIDDLE
    for _, l in ipairs(new_hunk) do block[#block + 1] = l end
    block[#block + 1] = MARKER_END

    -- Replace the old lines with the conflict block (0-based API)
    local buf_start = old_start - 1  -- convert to 0-based
    local buf_end = buf_start + old_count
    api.nvim_buf_set_lines(bufnr, buf_start, buf_end, false, block)
  end
end

--- Inject conflict markers for a file given its old and new content.
--- Opens or reuses the buffer for `filepath`, uses vim.diff() to compute
--- per-hunk changes (like avante.nvim), and inserts conflict markers only
--- around the changed sections.
---
--- @param filepath string  absolute or project-relative path
--- @param old_lines string[]  original file content (lines)
--- @param new_lines string[]  new/proposed content (lines)
--- @param opts? { on_complete: fun(content: string|nil) }  optional callbacks
function M.show(filepath, old_lines, new_lines, opts)
  define_highlights()
  opts = opts or {}

  -- Open / switch to the file buffer
  local bufnr = vim.fn.bufadd(filepath)
  vim.fn.bufload(bufnr)

  -- Find or create a window for this buffer
  local winid = vim.fn.bufwinid(bufnr)
  if winid == -1 then
    vim.cmd("vsplit")
    api.nvim_set_current_buf(bufnr)
  else
    api.nvim_set_current_win(winid)
  end

  -- Register on_complete callback if provided (review mode)
  if opts.on_complete then
    _on_complete_cbs[bufnr] = opts.on_complete
  end

  -- Set buffer content to old content first (so line numbers match)
  api.nvim_buf_set_lines(bufnr, 0, -1, false, old_lines)

  -- Compute per-hunk diff
  local old_text = table.concat(old_lines, "\n") .. "\n"
  local new_text = table.concat(new_lines, "\n") .. "\n"
  local hunks = minimize_diff(old_text, new_text)

  if #hunks == 0 then
    vim.notify("ShellGeist: no differences found", vim.log.levels.INFO, { title = "ShellGeist" })
    -- Clean up callback if no diffs — auto-approve with new content
    if opts.on_complete then
      _on_complete_cbs[bufnr] = nil
      opts.on_complete(table.concat(new_lines, "\n") .. "\n")
    end
    return
  end

  -- Insert per-hunk conflict markers (bottom-to-top)
  insert_conflict_contents(bufnr, hunks, old_lines, new_lines)

  -- Highlight & keymaps
  highlight_conflicts(bufnr)
  setup_keymaps(bufnr)

  -- Jump to first conflict
  local positions = scan(bufnr)
  if #positions > 0 then
    api.nvim_win_set_cursor(0, { positions[1].start + 1, 0 })
  end
end

--- Convenience wrapper: show inline diff using old/new content strings.
--- Called by the frontend when the agent writes a file.
--- @param filepath string  absolute path to the file
--- @param old_content string  old file content (string, not lines)
--- @param new_content string  new file content (string, not lines)
--- @param opts? { on_complete: fun(content: string|nil) }  optional callbacks
function M.show_inline(filepath, old_content, new_content, opts)
  local old_lines = vim.split(old_content or "", "\n", { plain = true })
  local new_lines = vim.split(new_content or "", "\n", { plain = true })
  M.show(filepath, old_lines, new_lines, opts)
end

--- Inject conflict markers for a file using a unified diff string.
--- Parses the diff to extract per-file old/new content, then calls show()
--- which now uses per-hunk insertion via vim.diff().
---
--- @param diff_text string  unified diff output (e.g. from git diff)
--- @param root string  project root path
function M.show_from_diff(diff_text, root)
  define_highlights()

  -- Parse unified diff into per-file hunks
  local files = M.parse_unified_diff(diff_text)
  if #files == 0 then
    vim.notify("ShellGeist: no changes found in diff", vim.log.levels.WARN, { title = "ShellGeist" })
    return
  end

  for _, file_diff in ipairs(files) do
    local filepath = file_diff.path
    if root and root ~= "" then
      local full = root .. "/" .. filepath
      if vim.fn.filereadable(full) == 1 then
        filepath = full
      end
    end

    -- Read current file content (this is the NEW content after changes)
    local current_lines = {}
    if vim.fn.filereadable(filepath) == 1 then
      current_lines = vim.fn.readfile(filepath)
    end

    -- Get old content via git show HEAD:file
    local old_cmd = { "git", "-C", root or ".", "show", "HEAD:" .. file_diff.path }
    local old_out = vim.fn.systemlist(old_cmd)
    if vim.v.shell_error ~= 0 then
      old_out = {}  -- new file, no old content
    end

    if #old_out == 0 and #current_lines == 0 then
      -- Nothing to show
    elseif #old_out == 0 then
      -- New file — show all as incoming
      M.show(filepath, {}, current_lines)
    else
      -- Use per-hunk diff (the new M.show handles it)
      M.show(filepath, old_out, current_lines)
    end
  end
end

--- Parse a unified diff string into per-file entries.
--- @param diff_text string
--- @return { path: string, hunks: string[] }[]
function M.parse_unified_diff(diff_text)
  local files = {}
  local current = nil
  for _, line in ipairs(vim.split(diff_text, "\n", { plain = true })) do
    local path = line:match("^diff %-%-git a/(.-) b/")
    if path then
      current = { path = path, hunks = {} }
      files[#files + 1] = current
    elseif current then
      table.insert(current.hunks, line)
    end
  end
  return files
end

return M
