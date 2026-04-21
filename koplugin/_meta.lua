local _ = require("gettext")
return {
    name        = "publish",
    fullname    = _("Publish"),
    description = _([[Automatically downloads new EPUBs from your home server on wake.
Drop files into the server inbox folder; the Kindle pulls them the next time it wakes up.]]),
}
