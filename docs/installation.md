# Installation

The add-on currently supports **Windows 10 and 11 (64-bit) only**.

## 1. Install Blackmagic Desktop Video

The add-on talks to the card through Blackmagic's driver, which provides the DeckLink API.

1. Download **Desktop Video** for Windows from
   [Blackmagic Design support](https://www.blackmagicdesign.com/support/family/capture-and-playback)
   and install it. Restart if the installer asks you to.
2. Open **Blackmagic Desktop Video Setup** and check that your device shows up. If the
   card has several connectors or profiles, choose the profile that gives you a playback
   output.

Nothing else is needed at run time. The DeckLink SDK is only needed to
[build from source](building.md).

## 2. Install the add-on

1. Download `blender_decklink_output-<version>-windows-x64.zip` from the project's
   **Releases** page. Keep it zipped.
2. Start Blender 5.0 or newer.
3. Open **Edit > Preferences > Get Extensions**.
4. Click the **⌄** drop-down at the top right of the panel and choose
   **Install from Disk…**.
5. Select the zip. Blender installs it into the *User Default* repository and enables it.

You can also drag the zip from Explorer onto the Blender window and confirm the install.

**Live Camera Output for Blackmagic Design DeckLink** should now be listed and ticked
under **Preferences > Add-ons**.

## 3. Check it works

1. Open **Properties > Output** (the printer icon) and expand **Live Camera Output**.
2. The first line should read *Desktop Video &lt;version&gt;, N device(s)*. If it reads
   *Blackmagic Desktop Video is not installed*, go back to step 1.
3. Click **+** to add an output, then tick **Output Enabled**. Your monitor should show the
   scene camera.

[usage.md](usage.md) explains the rest of the options.

## Updating

Install the new zip the same way. Blender replaces the old version. The add-on loads a copy
of its DLL from `%TEMP%`, so the install works while Blender is running. **Restart
Blender** to load the new native code.

## Uninstalling

Go to **Preferences > Add-ons**, expand **Live Camera Output for Blackmagic Design DeckLink**
and click **Uninstall**. The add-on leaves cached DLL copies in
`%TEMP%\blender_decklink_output`, which you can delete at any time.

## Add-on preferences

- **Start Outputs When a File Opens**: when it's off (the default), outputs saved as
  enabled open disabled, so opening a file never takes over a card by surprise. Turn it on
  for playout machines that should come up live.

## Troubleshooting

| Symptom | Fix |
|---|---|
| *Blackmagic Desktop Video is not installed* | Install Desktop Video, then click the refresh button in the panel or restart Blender. |
| *native library missing* | The zip was built without the DLL. Use a release zip, or build it (see [building.md](building.md)). |
| Device not listed | Check it in Desktop Video Setup. Another application may be holding it, or its profile may be capture-only. Click refresh. |
| *DeckLink device '…' not found* | The saved device name doesn't match this machine. Pick the device again. |
| Black output with Offscreen | Offscreen needs at least one 3D viewport open somewhere in the window. The status line says so. |
| Black output with Mirror | The mirrored viewport must be visible on screen and not covered. Use **New Window**. |
| Late or dropped frames | Raise **Pre-roll**, lower **Max Capture FPS**, or use **Update: On Change**. |
