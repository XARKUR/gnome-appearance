# Third-party resources

The MIT license in this project covers the original orchestration code only.

- **Orchis**: https://github.com/vinceliuice/Orchis-theme — the supplied source archive includes the upstream `COPYING` file and original copyright notices.
- **Tela**: https://github.com/vinceliuice/Tela-icon-theme — the supplied source archive includes the upstream `COPYING` file and original copyright notices.
- **GNOME User Themes**: https://gitlab.gnome.org/GNOME/gnome-shell-extensions and https://extensions.gnome.org/extension/19/user-themes/ — original source headers are retained. A GPL-2.0 license copy is included under `resources/licenses/`.
- **GNOME Shell 51.1 theme sources**: https://github.com/GNOME/gnome-shell/tree/51.1/data/theme — exact source files and original copyright/license headers are retained in `resources/shell-51/vendor/`. GNOME's `COPYING` and an LGPL-2.1 license copy are included. The archive hash is in `resources/shell-51/upstream.lock.json`.
- **Local Shell 51 Orchis port**: `resources/shell-51/ported/`, `orchis.scss` and `_orchis-overrides.scss` integrate the GNOME 51.1 widget rules with the existing Orchis palette/geometry and selected appearance treatments. These Sass changes are GPL-3.0-or-later; original GNOME/Orchis copyrights remain in force. They are not an official upstream Orchis GNOME 51 release.
- **User Themes 51**: official version-name 51.0 / EGO version 78, https://extensions.gnome.org/review/75038 — packaged unchanged, GPL-2.0-or-later, with the upstream `COPYING` and source notices intact.
- **Breeze cursors 5.27.5**: https://invent.kde.org/plasma/breeze — compiled cursors come unchanged from Debian's `breeze-cursor-theme` 4:5.27.5-2 package, installed under the name `Breeze`. `resources/breeze-source.tar.xz` contains the corresponding KDE cursor sources, build files and upstream notices (other Breeze components omitted). `resources/breeze.lock.json` records exact URLs and hashes. Artwork is LGPL-3.0-or-later according to Debian's licensing clarification, retained in `resources/licenses/breeze-debian-copyright.txt`; upstream file-level notices and COPYING are preserved. LGPL-3.0 and GPL-3.0 texts are included in `resources/licenses/`.

Resource versions and hashes are recorded in `resources/manifest.json`. None of these third-party resources are relicensed under this project's MIT license.


Tela alias recovery: resources/tela-link-recovery.json records symlink targets
from vinceliuice/Tela-icon-theme commit 4f560eccd0acc9c192f31077c2b3202422cb24fb.
https://github.com/vinceliuice/Tela-icon-theme/tree/4f560eccd0acc9c192f31077c2b3202422cb24fb
The supplied snapshot lost 1,471 link entries as zero-byte SVG files. The
10,754 nonempty source files compared are identical to this upstream revision.
Only the temporary build copy is repaired; upstream attribution and licensing
remain unchanged. The recovery file includes the source archive SHA256.
