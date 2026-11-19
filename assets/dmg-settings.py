import os

application = defines.get("app", "dist/GAPs.app")
appname = os.path.basename(application)

format = "UDZO"
filesystem = "HFS+"
files = [application]
symlinks = {"Applications": "/Applications"}
icon = "assets/icon.icns"
background = "assets/dmg-background.tiff"

show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
window_rect = ((200, 120), (660, 432))
default_view = "icon-view"
show_icon_preview = False
icon_size = 128
text_size = 13
icon_locations = {
    appname: (165, 160),
    "Applications": (495, 160),
}
