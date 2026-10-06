# MANGA-CLI — Android / Termux Beta

**A lightweight, retro terminal manga reader, now running on Android through Termux.**  
*a comadreja project* — inspired by [ani-cli](https://github.com/pystardust/ani-cli).

> **Android status:** `v0.8.3-android` is a beta branch built from the stable desktop `v0.8.3` codebase.  
> It has been tested successfully on a real Android device and is already smooth and usable for daily reading, but it is not yet claimed to work on every Android device or Termux:X11 configuration.

## What is this branch?

The stable desktop release remains **manga-cli v0.8.3** on `main`.

This parallel branch, **`android-v0.8.3`**, keeps the same manga-cli core — search, sources, saved manga, history, chapter handling, progress, cache and settings — and replaces only the display/input layer with a mobile-oriented reader for:

```text
Android
  -> Termux
  -> manga-cli
  -> mpv-x
  -> Termux:X11
```

The goal is not to turn manga-cli into a native Android application. The goal is to keep the lightweight terminal project intact while making it genuinely usable on a phone.

The Android profile has a dedicated touch reader with:

- large left/right tap zones for page navigation;
- double-tap zoom centered around the touched area;
- smooth panning while zoomed;
- fit-to-screen page rendering;
- chapter-to-chapter navigation;
- Saved manga;
- History;
- reading progress;
- Continue/resume support;
- the same MangaKatana / MangaPill source engine as desktop;
- the same terminal interface and color themes as manga-cli desktop.

The Android profile does **not** modify global Termux:X11 orientation preferences. Manga reading is primarily designed around portrait use, while Termux:X11 remains free for other applications.

---

## Current versions

| Version | Platform | Status |
| --- | --- | --- |
| `v0.8.3` | Linux desktop | Stable |
| `v0.8.3-android` | Android / native Termux | Beta, real-device tested |
| `v0.9.0` | Desktop + Android | Planned unified release |

The Android branch will remain separate for the 0.8.3 generation so the stable desktop version is not disturbed.

The plan for **v0.9.0** is to merge both profiles into one codebase and use platform detection during installation. A future installer should be able to detect a desktop Linux environment or native Termux automatically, while still allowing an explicit platform override.

---

# Android installation

## 1. Install Termux

Use a current official Termux build.

Do not mix Termux packages from unrelated distribution sources. If you choose the optional Termux:X11 `sharedUid` APK, the Termux:X11 project notes that it requires the GitHub-signed Termux build.

This Android profile targets **native Termux**, not a Debian/Ubuntu PRoot container.

---

## 2. Install the Termux:X11 Android app

Termux:X11 consists of **two parts**:

1. the Android Termux:X11 app;
2. the companion package installed inside Termux.

The official Termux:X11 project provides the Android APK through its nightly releases:

https://github.com/termux/termux-x11/releases/tag/nightly

The regular universal APK is:

```text
termux-x11-universal-debug.apk
```

Install that APK on Android before continuing.

Termux:X11 currently requires Android 8 or newer according to the upstream project.

---

## 3. Install the required Termux packages

Open Termux and run:

```bash
pkg update
pkg install -y python git x11-repo
pkg install -y mpv-x termux-x11-nightly
```

This installs:

- Python;
- Git;
- the Termux graphical/X11 repository;
- `mpv-x`;
- the Termux:X11 companion command.

No pip packages are required by manga-cli itself.

---

## 4. Clone the Android branch

The recommended public branch for the current Android beta is:

```bash
git clone --branch android-v0.8.3 --single-branch \
  https://github.com/comadrejarottingitself/manga-cli.git \
  manga-cli-android
```

Enter the repository:

```bash
cd manga-cli-android
```

You can verify the branch with:

```bash
git branch --show-current
```

Expected result:

```text
android-v0.8.3
```

There is also a `v0.8.3-android` tag marking the validated Android code snapshot.

---

## 5. Install manga-cli for Termux

From inside the cloned Android branch:

```bash
bash termux-install.sh
```

The installer checks that it is running inside native Termux and verifies the required commands before installing the Android profile.

Application code is installed under:

```text
~/.local/lib/manga-cli-android-v083
```

The public launcher is installed as:

```text
$PREFIX/bin/manga-termux
```

The Android installer intentionally does **not** run the Debian desktop installer.

It also does not delete or rewrite your private manga-cli reading state or cache.

---

## 6. Run it

Start manga-cli with:

```bash
manga-termux
```

The launcher starts a Termux:X11 server on display `:1` when necessary, exports the correct display and launches manga-cli.

Then open the **Termux:X11 Android app** to see the reader.

Normal flow:

```text
Termux
  -> manga-termux
  -> manga-cli menu
  -> select manga/chapter
  -> Termux:X11 reader
```

---

# Android reader controls

The Android profile intentionally uses a simpler interaction model than the desktop Page/Width reader.

| Gesture / input | Action |
| --- | --- |
| Tap right side | Next page |
| Tap left side | Previous page |
| Tap center | Show/toggle reader status |
| Double tap | Zoom around the touched point |
| Double tap while zoomed | Reset to fit-to-screen |
| Drag while zoomed | Pan around the page |
| `Q` / `Esc` | Save progress and leave reader |

The left and right navigation zones are deliberately large so page turning works comfortably with one hand.

While zoomed, normal taps do not accidentally turn the page. Zoom and pan are temporary mobile view state; chapter/page progress remains persistent.

Keyboard and mouse fallbacks are kept for debugging and for people using Termux:X11 with external input devices.

---

# Reading progress and Continue

The Android profile keeps manga-cli's normal state system.

That means the following survive reader exits and later sessions:

- selected manga;
- current chapter;
- current page;
- Saved entries;
- History;
- settings;
- Continue/resume state.

During real-device validation, exiting and reopening manga-cli correctly returned to the expected manga, chapter and page.

---

# Sources

Current source adapters:

- **MangaKatana**
- **MangaPill**

Automatic source selection and fallback remain part of the same manga-cli core used by the desktop release.

External manga websites can change independently from this project, so a parser may occasionally require maintenance even when the application itself is working correctly.

MANGA-CLI does not bundle manga-site content and is not affiliated with MangaKatana or MangaPill. Users are responsible for following applicable terms and content rights.

---

# Settings and appearance

The Android branch keeps the normal manga-cli terminal interface.

Available accent colors:

```text
Crimson
Red
Orange
Amber
Green
Lime
Cyan
Blue
Purple
Magenta
```

Crimson is the default.

The project keeps its black-background, character-frame, monospace terminal style and does not install fonts or modify the user's terminal theme.

Android-specific reader settings include configurable double-tap zoom strength.

---

# Uninstall

To remove the Android application code and launcher:

```bash
cd manga-cli-android
bash termux-uninstall.sh
```

The uninstaller removes the Android application files managed by this profile.

It intentionally leaves Saved manga, History, settings, cache and other private reading state untouched.

---

# Updating this beta branch

If a documented update is published to the branch, update the source checkout first:

```bash
cd manga-cli-android
git pull --ff-only
```

Then reinstall:

```bash
bash termux-install.sh
```

For long-term development, the Android and desktop profiles are planned to converge in `v0.9.0`.

---

# Diagnostics

Useful manga-cli commands remain available through the Android launcher:

```bash
manga-termux --version
manga-termux --self-test
manga-termux --doctor
manga-termux --network-test
```

The offline self-test does not require network access.

Network/source diagnostics can include local paths or manga-title information, so review their output before posting logs publicly.

To verify that the required Android-side commands exist:

```bash
command -v python
command -v mpv
command -v termux-x11
command -v manga-termux
```

---

# Termux:X11 troubleshooting

## Termux:X11 app is not installed

Installing only:

```bash
pkg install termux-x11-nightly
```

is not enough.

The Termux:X11 project requires both the companion Termux package **and** the Android APK.

Official upstream project:

https://github.com/termux/termux-x11

---

## Reader does not appear

Check that Termux:X11 is installed and that the server can start:

```bash
termux-x11 :1
```

The manga launcher normally handles this automatically.

If an old X11 server is stuck, you can stop it with:

```bash
pkill -f termux-x11
```

and then run:

```bash
manga-termux
```

again.

---

## Black X11 screen on a particular device

The upstream Termux:X11 documentation notes that some devices may require its `-legacy-drawing` mode.

This is a device-specific Termux:X11 issue rather than a manga-cli reader mode. Consult the official Termux:X11 documentation before changing display options.

---

## Orientation

manga-cli does **not** change global Termux:X11 orientation preferences.

The reader is primarily intended for portrait manga reading. Global Termux:X11 settings are shared with other X11 applications, so the Android profile deliberately avoids rewriting them.

---

# Validation status

This branch is still labelled **beta** because Android hardware, vendor ROMs and Termux:X11 behavior vary between devices.

However, this is not an untested prototype.

The current Android branch has passed:

- Android-specific automated tests;
- the full project test suite on native Termux;
- the full project test suite on Debian;
- the offline manga-cli self-test;
- release privacy checks;
- release checksum verification;
- real Android device installation;
- real Termux:X11 + `mpv-x` rendering;
- touch previous/next page navigation;
- repeated page turns;
- double-tap zoom;
- zoomed panning;
- chapter navigation;
- clean exit and reopening;
- Continue/progress restoration;
- repeated-use/stress interaction testing.

Validation snapshot for this branch:

```text
Native Termux:
  465 tests -> OK
  55 platform-inapplicable tests skipped
  self-test -> 55/55 OK

Debian:
  465 tests -> OK
  5 platform-inapplicable tests skipped
  self-test -> 55/55 OK

Release checks:
  privacy check -> OK
  generated messages -> OK
  SHA256SUMS -> OK (99 approved payload files)
```

A successful test on one real phone does not guarantee compatibility with every Android device, which is why the Android build remains a beta until the desktop and Android architectures are unified and tested more broadly.

---

# Desktop version

If you are using a normal Linux desktop, use the stable `main` branch / `v0.8.3` release instead of this Android profile.

Desktop v0.8.3 focuses on stability, installer hardening and broader Linux compatibility work while keeping the established Page/Width reader behavior.

Confirmed desktop validation currently includes Debian.

Desktop repository branch:

```text
main
```

Android repository branch:

```text
android-v0.8.3
```

Do not merge the Android compatibility branch into your desktop installation manually.

---

# Roadmap: v0.9.0

The 0.8.3 generation deliberately keeps desktop and Android separate.

The intended architecture for the next major development step is:

```text
manga-cli 0.9
      |
      +-- platform detection
      |
      +-- Linux desktop
      |     -> desktop reader
      |     -> desktop dependencies
      |
      +-- native Termux / Android
            -> mobile reader
            -> mpv-x + Termux:X11
```

The future installer is planned to detect the platform automatically and ask for confirmation, while also providing an explicit override for unusual setups.

Conceptually:

```text
Detected platform: Android / Termux
Install Android profile? [Y/n]
```

or:

```text
Detected platform: Linux desktop
Install desktop profile? [Y/n]
```

The important point is that Saved manga, History, source logic, progress, parsing and the rest of the application should remain one shared codebase. Only the platform-specific display/input and installation pieces should differ.

---

# Development

Run the complete test suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -v
```

Run the offline self-test with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 manga.py --self-test
```

Check the release payload with:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 tools/build_release.py --check
```

The project intentionally uses the Python standard library for its runtime code and does not require a pip dependency stack.

See also:

- [`docs/ANDROID_TERMUX.md`](docs/ANDROID_TERMUX.md)
- [`docs/ANDROID_READER_DESIGN.md`](docs/ANDROID_READER_DESIGN.md)
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`VALIDATION.md`](VALIDATION.md)
- [`SECURITY.md`](SECURITY.md)
- [`CONTRIBUTING.md`](CONTRIBUTING.md)

---

# Privacy

The public repository intentionally excludes reading state, settings, history, progress, cached manga pages, logs, backups, credentials and personal home paths.

Release tooling includes privacy and payload checks, but contributors should still inspect staged files and Git commit metadata before publishing changes.

---

# License

MANGA-CLI is open source under the **MIT License**.

See [`LICENSE`](LICENSE) and [`THIRD_PARTY.txt`](THIRD_PARTY.txt).

---

**manga-cli v0.8.3** — stable desktop release  
**manga-cli v0.8.3-android** — Android / Termux beta

*a comadreja project*
