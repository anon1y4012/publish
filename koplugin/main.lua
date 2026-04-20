--[[
epubsync.koplugin
-----------------
On device wake/resume, connects to your home server and downloads any
new EPUBs that aren't already present in the local inbox folder.

Server API (see server/epubsync_server.py):
  GET /manifest          → JSON: { "files": ["book.epub", ...] }
  GET /download/<file>   → raw EPUB bytes

Config is stored in KOReader's settings under "epubsync".
--]]

local WidgetContainer = require("ui/widget/container/widgetcontainer")
local NetworkMgr     = require("ui/network/manager")
local DataStorage    = require("datastorage")
local UIManager      = require("ui/uimanager")
local InfoMessage    = require("ui/widget/infomessage")
local Event          = require("ui/event")
local logger         = require("logger")
local json           = require("json")
local lfs            = require("libs/libkoreader-lfs")
local http           = require("socket/http")
local ltn12          = require("ltn12")
local _              = require("gettext")

-- ---------------------------------------------------------------------------
-- Plugin definition
-- ---------------------------------------------------------------------------

local EpubSync = WidgetContainer:extend{
    name        = "epubsync",
    is_doc_only = false,
}

-- Default settings (overridden by user config)
local DEFAULTS = {
    server_url  = "https://your-tunnel.trycloudflare.com",  -- no trailing slash
    inbox_dir   = "/mnt/us/epubsync",                       -- Kindle path; change for Kobo etc.
    api_token   = "",                                        -- shared secret header value
    auto_sync   = true,                                      -- sync on every resume
    notify      = true,                                      -- show toast on completion
}

-- ---------------------------------------------------------------------------
-- Lifecycle
-- ---------------------------------------------------------------------------

function EpubSync:init()
    self.settings = G_reader_settings:readSetting("epubsync") or {}
    -- Merge defaults for any missing keys
    for k, v in pairs(DEFAULTS) do
        if self.settings[k] == nil then
            self.settings[k] = v
        end
    end

    -- Register menu entry (appears in Tools menu)
    self.ui.menu:registerToMainMenu(self)
end

function EpubSync:onResume()
    if self.settings.auto_sync then
        -- willRerunWhenOnline defers the callback until WiFi is up,
        -- showing a "Connecting..." UI if needed. Returns true and
        -- schedules the callback if wifi was off; returns false if
        -- already online (so we call manually in that case).
        if not NetworkMgr:willRerunWhenOnline(function()
            self:_doSync(false)
        end) then
            self:_doSync(false)
        end
    end
end

-- Also run when KOReader first starts (file manager view)
function EpubSync:onStart()
    self:onResume()
end

-- ---------------------------------------------------------------------------
-- Core sync logic
-- ---------------------------------------------------------------------------

function EpubSync:_doSync(interactive)
    local cfg = self.settings

    -- Ensure inbox directory exists
    lfs.mkdir(cfg.inbox_dir)

    -- ---- 1. Fetch manifest ------------------------------------------------
    local manifest_url = cfg.server_url .. "/manifest"
    local resp_body    = {}
    local headers      = { ["X-EpubSync-Token"] = cfg.api_token }

    local ok, code = http.request({
        url     = manifest_url,
        method  = "GET",
        headers = headers,
        sink    = ltn12.sink.table(resp_body),
    })

    if not ok or code ~= 200 then
        logger.warn("EpubSync: manifest fetch failed, code=", code)
        if interactive then
            UIManager:show(InfoMessage:new{
                text    = _("EpubSync: could not reach server.\nCheck URL and token in settings."),
                timeout = 4,
            })
        end
        return
    end

    local manifest
    local parse_ok, err = pcall(function()
        manifest = json.decode(table.concat(resp_body))
    end)
    if not parse_ok or not manifest or not manifest.files then
        logger.warn("EpubSync: bad manifest JSON:", err)
        return
    end

    -- ---- 2. Diff against local inbox -------------------------------------
    local local_files = {}
    for entry in lfs.dir(cfg.inbox_dir) do
        local_files[entry] = true
    end

    local to_download = {}
    for _, fname in ipairs(manifest.files) do
        if not local_files[fname] then
            table.insert(to_download, fname)
        end
    end

    if #to_download == 0 then
        logger.dbg("EpubSync: inbox up to date, nothing to download")
        if interactive then
            UIManager:show(InfoMessage:new{
                text    = _("EpubSync: already up to date."),
                timeout = 2,
            })
        end
        return
    end

    -- ---- 3. Download each new file ---------------------------------------
    local downloaded = 0
    local failed     = 0

    for _, fname in ipairs(to_download) do
        local dest_path = cfg.inbox_dir .. "/" .. fname
        local out_file, open_err = io.open(dest_path, "wb")
        if not out_file then
            logger.warn("EpubSync: cannot open", dest_path, "for writing:", open_err)
            failed = failed + 1
        else
            -- URL-encode just the filename (handle spaces, etc.)
            local safe_fname = fname:gsub("([^%w%-_%.~])", function(c)
                return string.format("%%%02X", string.byte(c))
            end)
            local dl_url = cfg.server_url .. "/download/" .. safe_fname
            local dl_ok, dl_code = http.request({
                url     = dl_url,
                method  = "GET",
                headers = headers,
                sink    = ltn12.sink.file(out_file),
            })
            -- ltn12.sink.file closes the file on completion
            if not dl_ok or dl_code ~= 200 then
                logger.warn("EpubSync: download failed for", fname, "code=", dl_code)
                os.remove(dest_path)   -- clean up partial file
                failed = failed + 1
            else
                logger.info("EpubSync: downloaded", fname)
                downloaded = downloaded + 1
            end
        end
    end

    -- ---- 4. Refresh file browser if anything was downloaded --------------
    if downloaded > 0 then
        UIManager:broadcastEvent(Event:new("FileManagerRefresh"))
    end

    -- ---- 5. Notify -------------------------------------------------------
    if cfg.notify or interactive then
        local msg
        if failed == 0 then
            msg = string.format(_("EpubSync: downloaded %d new book(s)."), downloaded)
        else
            msg = string.format(
                _("EpubSync: %d downloaded, %d failed. Check log."),
                downloaded, failed
            )
        end
        UIManager:show(InfoMessage:new{ text = msg, timeout = 3 })
    end
end

-- ---------------------------------------------------------------------------
-- Settings menu (Tools → EpubSync)
-- ---------------------------------------------------------------------------

function EpubSync:addToMainMenu(menu_items)
    menu_items.epubsync = {
        text = _("EpubSync"),
        sub_item_table = {
            {
                text = _("Sync now"),
                callback = function()
                    if not NetworkMgr:willRerunWhenOnline(function()
                        self:_doSync(true)
                    end) then
                        self:_doSync(true)
                    end
                end,
            },
            {
                text_func = function()
                    return self.settings.auto_sync
                        and _("Auto-sync on wake: ON")
                        or  _("Auto-sync on wake: OFF")
                end,
                callback = function()
                    self.settings.auto_sync = not self.settings.auto_sync
                    self:_saveSettings()
                end,
            },
            {
                text_func = function()
                    return self.settings.notify
                        and _("Notifications: ON")
                        or  _("Notifications: OFF")
                end,
                callback = function()
                    self.settings.notify = not self.settings.notify
                    self:_saveSettings()
                end,
            },
            {
                text = _("Server URL…"),
                callback = function()
                    self:_promptSetting(
                        _("Server URL (no trailing slash)"),
                        "server_url",
                        _("https://your-tunnel.trycloudflare.com")
                    )
                end,
            },
            {
                text = _("API token…"),
                callback = function()
                    self:_promptSetting(
                        _("API token (shared secret)"),
                        "api_token",
                        _("leave blank to disable auth")
                    )
                end,
            },
            {
                text = _("Inbox folder…"),
                callback = function()
                    self:_promptSetting(
                        _("Local inbox path"),
                        "inbox_dir",
                        _("/mnt/us/epubsync")
                    )
                end,
            },
        },
    }
end

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

function EpubSync:_saveSettings()
    G_reader_settings:saveSetting("epubsync", self.settings)
end

function EpubSync:_promptSetting(title, key, hint)
    local InputDialog = require("ui/widget/inputdialog")
    local dialog
    dialog = InputDialog:new{
        title       = title,
        input       = self.settings[key] or "",
        input_hint  = hint,
        buttons = {{
            {
                text = _("Cancel"),
                callback = function() UIManager:close(dialog) end,
            },
            {
                text     = _("Save"),
                is_enter_default = true,
                callback = function()
                    self.settings[key] = dialog:getInputText()
                    self:_saveSettings()
                    UIManager:close(dialog)
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

return EpubSync
