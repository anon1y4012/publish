--[[
publish.koplugin
----------------
On device wake/resume, waits for WiFi to associate naturally (no prompts),
then syncs with the Publish server. Only notifies if new books were delivered.

Server API:
  GET /manifest          → JSON: { "files": ["book.epub", ...] }
  GET /download/<file>   → raw EPUB bytes

Config stored in KOReader settings under "publish".
--]]

local WidgetContainer = require("ui/widget/container/widgetcontainer")
local NetworkMgr      = require("ui/network/manager")
local UIManager       = require("ui/uimanager")
local InfoMessage     = require("ui/widget/infomessage")
local Event           = require("ui/event")
local logger          = require("logger")
local json            = require("json")
local lfs             = require("libs/libkoreader-lfs")
local http            = require("socket/http")
local ltn12           = require("ltn12")
local socket          = require("socket")
local _               = require("gettext")

-- ---------------------------------------------------------------------------
-- Plugin
-- ---------------------------------------------------------------------------

local Publish = WidgetContainer:extend{
    name        = "publish",
    is_doc_only = false,
}

local DEFAULTS = {
    server_url   = "https://mini-publish.silentmail.org",
    inbox_dir    = "/mnt/us/books",
    api_token    = "",
    auto_sync    = true,
    -- How long to wait after wake before attempting sync (seconds).
    -- Gives WiFi time to associate without any prompts.
    wake_delay   = 8,
    -- How many seconds to keep retrying if the first attempt fails
    -- (covers slow DHCP / captive portal situations).
    retry_window = 30,
    retry_interval = 5,
}

-- ---------------------------------------------------------------------------
-- Init
-- ---------------------------------------------------------------------------

function Publish:init()
    self.settings = G_reader_settings:readSetting("publish") or {}
    for k, v in pairs(DEFAULTS) do
        if self.settings[k] == nil then
            self.settings[k] = v
        end
    end
    self.ui.menu:registerToMainMenu(self)
end

-- ---------------------------------------------------------------------------
-- Wake / resume entry point
-- ---------------------------------------------------------------------------

function Publish:onResume()
    if not self.settings.auto_sync then return end

    -- Schedule the sync attempt after wake_delay seconds.
    -- UIManager:scheduleIn is non-blocking — the user sees their book
    -- or home screen immediately with no WiFi prompt.
    UIManager:scheduleIn(self.settings.wake_delay, function()
        self:_syncWhenReady()
    end)
end

function Publish:onStart()
    -- On first launch give a slightly shorter delay since WiFi may already be up.
    if not self.settings.auto_sync then return end
    UIManager:scheduleIn(3, function()
        self:_syncWhenReady()
    end)
end

-- ---------------------------------------------------------------------------
-- WiFi-aware sync with retry loop
-- ---------------------------------------------------------------------------

function Publish:_syncWhenReady()
    -- If WiFi is completely disabled, use willRerunWhenOnline so KOReader
    -- handles the "turn on WiFi?" flow the way the user has it configured.
    -- We do NOT call this when WiFi is already enabled but just associating —
    -- that case is handled by the retry loop below.
    if NetworkMgr.wifi_was_on == false then
        -- WiFi was explicitly off; let KOReader decide whether to prompt.
        NetworkMgr:willRerunWhenOnline(function()
            self:_doSync(false)
        end)
        return
    end

    -- WiFi is enabled (or state unknown). Attempt sync; retry on failure
    -- to cover the window while DHCP / association completes.
    local deadline = socket.gettime() + self.settings.retry_window
    local attempt

    attempt = function()
        if NetworkMgr:isOnline() then
            self:_doSync(false)
        elseif socket.gettime() < deadline then
            UIManager:scheduleIn(self.settings.retry_interval, attempt)
        else
            logger.info("Publish: WiFi did not come up within retry window, skipping sync")
        end
    end

    attempt()
end

-- ---------------------------------------------------------------------------
-- Core sync
-- ---------------------------------------------------------------------------

function Publish:_doSync(interactive)
    local cfg = self.settings

    -- Ensure inbox exists
    lfs.mkdir(cfg.inbox_dir)

    -- 1. Fetch manifest -------------------------------------------------------
    local manifest_url = cfg.server_url .. "/manifest"
    local resp_body    = {}
    local headers      = { ["X-Publish-Token"] = cfg.api_token }

    local ok, code = http.request({
        url     = manifest_url,
        method  = "GET",
        headers = headers,
        sink    = ltn12.sink.table(resp_body),
        timeout = 15,
    })

    if not ok or code ~= 200 then
        logger.warn("Publish: manifest fetch failed, code=", code)
        if interactive then
            UIManager:show(InfoMessage:new{
                text    = _("Publish: could not reach server.\nCheck URL and token in settings."),
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
        logger.warn("Publish: bad manifest JSON:", err)
        return
    end

    -- 2. Diff against local inbox --------------------------------------------
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
        -- Nothing new — stay silent. No toast, no notification.
        logger.dbg("Publish: inbox up to date")
        if interactive then
            UIManager:show(InfoMessage:new{
                text    = _("Publish: already up to date."),
                timeout = 2,
            })
        end
        return
    end

    -- 3. Download new files --------------------------------------------------
    local downloaded = 0
    local failed     = 0

    for _, fname in ipairs(to_download) do
        local dest_path = cfg.inbox_dir .. "/" .. fname
        local out_file, open_err = io.open(dest_path, "wb")
        if not out_file then
            logger.warn("Publish: cannot open", dest_path, ":", open_err)
            failed = failed + 1
        else
            local safe_fname = fname:gsub("([^%w%-_%.~])", function(c)
                return string.format("%%%02X", string.byte(c))
            end)
            local dl_url = cfg.server_url .. "/download/" .. safe_fname
            local dl_ok, dl_code = http.request({
                url     = dl_url,
                method  = "GET",
                headers = headers,
                sink    = ltn12.sink.file(out_file),
                timeout = 60,
            })
            if not dl_ok or dl_code ~= 200 then
                logger.warn("Publish: download failed for", fname, "code=", dl_code)
                os.remove(dest_path)
                failed = failed + 1
            else
                logger.info("Publish: downloaded", fname)
                downloaded = downloaded + 1
            end
        end
    end

    -- 4. Refresh file browser ------------------------------------------------
    if downloaded > 0 then
        UIManager:broadcastEvent(Event:new("FileManagerRefresh"))
    end

    -- 5. Notify — only if something actually arrived -------------------------
    -- We never show a toast when there's nothing new (handled by early return
    -- above). Here we only show on success or partial failure.
    if downloaded > 0 or (interactive and failed > 0) then
        local msg
        if failed == 0 then
            if downloaded == 1 then
                -- Show the actual filename so the user knows what arrived
                msg = string.format(_("New book delivered:\n%s"), to_download[1])
            else
                msg = string.format(_("%d new books delivered."), downloaded)
            end
        else
            msg = string.format(
                _("Publish: %d delivered, %d failed."),
                downloaded, failed
            )
        end
        UIManager:show(InfoMessage:new{
            text    = msg,
            timeout = 4,
        })
    end
end

-- ---------------------------------------------------------------------------
-- Settings menu  (Tools → Publish)
-- ---------------------------------------------------------------------------

function Publish:addToMainMenu(menu_items)
    menu_items.publish = {
        text = _("Publish"),
        sub_item_table = {
            {
                text = _("Sync now"),
                callback = function()
                    if NetworkMgr:isOnline() then
                        self:_doSync(true)
                    else
                        NetworkMgr:willRerunWhenOnline(function()
                            self:_doSync(true)
                        end)
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
                    return string.format(_("Wake delay: %ds"), self.settings.wake_delay)
                end,
                callback = function()
                    self:_promptNumber(
                        _("Seconds to wait after wake before syncing"),
                        "wake_delay", 1, 60
                    )
                end,
            },
            {
                text = _("Server URL…"),
                callback = function()
                    self:_promptSetting(
                        _("Server URL (no trailing slash)"),
                        "server_url",
                        _("https://mini-publish.silentmail.org")
                    )
                end,
            },
            {
                text = _("API token…"),
                callback = function()
                    self:_promptSetting(
                        _("API token"),
                        "api_token",
                        _("your PUBLISH_TOKEN value")
                    )
                end,
            },
            {
                text = _("Inbox folder…"),
                callback = function()
                    self:_promptSetting(
                        _("Local inbox path"),
                        "inbox_dir",
                        _("/mnt/us/books")
                    )
                end,
            },
        },
    }
end

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

function Publish:_saveSettings()
    G_reader_settings:saveSetting("publish", self.settings)
end

function Publish:_promptSetting(title, key, hint)
    local InputDialog = require("ui/widget/inputdialog")
    local dialog
    dialog = InputDialog:new{
        title      = title,
        input      = self.settings[key] or "",
        input_hint = hint,
        buttons = {{
            {
                text     = _("Cancel"),
                callback = function() UIManager:close(dialog) end,
            },
            {
                text             = _("Save"),
                is_enter_default = true,
                callback         = function()
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

function Publish:_promptNumber(title, key, min, max)
    local InputDialog = require("ui/widget/inputdialog")
    local dialog
    dialog = InputDialog:new{
        title      = title,
        input      = tostring(self.settings[key] or DEFAULTS[key]),
        input_type = "number",
        buttons = {{
            {
                text     = _("Cancel"),
                callback = function() UIManager:close(dialog) end,
            },
            {
                text             = _("Save"),
                is_enter_default = true,
                callback         = function()
                    local v = tonumber(dialog:getInputText())
                    if v and v >= min and v <= max then
                        self.settings[key] = v
                        self:_saveSettings()
                    end
                    UIManager:close(dialog)
                end,
            },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

return Publish
