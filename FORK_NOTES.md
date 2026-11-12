# ha1fdan fork: headless repeater with web panel + self-hosted OTA

Fork of [agessaman/MeshCore](https://github.com/agessaman/MeshCore) (`observer-firmware`
branch, the MQTT Observer firmware). Work branch and fork default: `webconfig-autostart-lan`.

Built for `DK_5230_R01_Hjallese`, a Heltec V3 in the loft at 192.168.1.175 on a smart
plug, with no USB access and no companion node. Every change here exists so it can be
managed and updated without the ladder.

## What the fork adds

| Change | Where | Why |
|---|---|---|
| `WEBCONFIG_AUTOSTART_LAN` | `examples/simple_repeater/MyMesh.cpp` (loop) | Upstream only serves the web panel after `start webconfig` (serial or LoRa login). This starts it once WiFi connects, every boot, retrying every 30 s. A later `stop webconfig` sticks until reboot. |
| OTA rollback guard | `MyMesh.cpp` (`verifyRollbackLater`, loop) | Arduino marks a new OTA image valid at boot. Instead it stays on probation until the panel has been up 60 s. A crash before that, or 10 min without the panel, boots the previous image. |
| `OTA_CHECK_HTTPS` | `src/helpers/ESP32Board.cpp` | Upstream fetches the manifest over plain HTTP for `ota check` and the `ota update` pre-check. GitHub Pages redirects HTTP to HTTPS (301), so OTA never worked. With this flag the check uses the CA-verified TLS path. |
| Env `Heltec_v3_repeater_observer_mqtt_webui` | `variants/heltec_v3/platformio.ini` | Stock V3 observer env + `WEBCONFIG_AUTOSTART_LAN`. Stock envs are unchanged. |
| `.github/workflows/ota-pages.yml` | | Builds every `*_webui` env and publishes the OTA channel to GitHub Pages. |

## OTA channel

- Manifest: `https://ha1fdan.github.io/MeshCore/v/<env>.json`
- Firmware: `https://ha1fdan.github.io/MeshCore/fw/<env>-v<base>.<build>-<hash>.bin` (+ `-merged.bin`)
- Every push to `webconfig-autostart-lan` (non-docs) or a manual run publishes a new build.
  Build number = workflow run number, so it always goes up; the device only updates
  when the manifest's `build` is higher than its own.
- Base version is read from upstream's `build-observer-firmwares.yml`, so it follows rebases.
- All channel URLs are baked into the binary: `ota branch prod` = this site,
  `ota branch beta` = `/beta/v` (empty, so it 404s). The node can never pull stock firmware.
- The device needs HTTPS with a public CA and **no redirects** for the `.bin`. That's why
  this uses Pages, not GitHub Release assets (those redirect).
- Firmware isn't signed: HTTPS + control of this GitHub account is the only protection.

## Day to day

Web panel: http://192.168.1.175/ (admin password). Its command line runs any CLI command.

Update firmware:
```
ota check     # update available: v1.18.0.N -> v1.18.0.M (… behind)
ota update    # downloads, flashes, reboots (~30 s), probation 60 s, then confirmed
```

Pull upstream changes and publish:
```
cd ~/projects/MeshCore
git fetch upstream && git rebase upstream/observer-firmware && git push --force-with-lease
```
Then wait for the "Build OTA Firmware to GitHub Pages" run and do `ota update` in the panel.

## Don'ts (lockout risks)

- **Never `set bridge.enabled off`.** The bridge task brings WiFi up; off = no WiFi = no panel.
- **Don't change WiFi SSID/password or the router** without updating the node first.
  Keep a DHCP reservation for 192.168.1.175.
- Don't push to `observer-firmware`, `observer-firmware-dev` or `main` on this fork, or push
  `companion-*` / `repeater-*` / `room-server-*` tags: upstream's release/deploy workflows
  are still enabled here and fire on those. Disable them in the Actions tab if needed.
- A companion node (any second board) is the real fallback: LoRa remote admin works
  even with WiFi gone.

## USB flashing (only if OTA is impossible)

Flash the CI merged image (keeps settings; they live at 0x670000, image ends ~0x1A9000):
```
pio pkg exec -p tool-esptoolpy -- esptool.py --chip esp32s3 --port /dev/ttyUSB0 \
  --baud 921600 write_flash 0x0 <env>-v<ver>-<hash>-merged.bin
```
Close miniterm / browser serial tabs first, or the port is locked.
Board check: the V3 has 8 MB flash. V4 images (16 MB) boot-loop on it with
`Detected size(8192k) smaller than the size in the binary image header(16384k)`.

## Node config (for reference)

```
set name DK_5230_R01_Hjallese
set radio 869.618,62.5,8,8      # EU/UK Narrow, what all of Fyn runs
set tx 22                       # V3, no PA: 22 dBm + 6.5 dBi MikroTik omni ≈ 430 mW ERP (limit 500)
set mqtt.iata ODE
set timezone Europe/Copenhagen
set mqtt1.preset none           # no upload to Let's Mesh
set mqtt2.preset custom         # own broker: CT 110 meshcore-observer (~/projects/meshcore-observer)
set mqtt2.server mqtt://192.168.1.236:1883
set mqtt2.username repeater     # password in /etc/meshcore-observer/repeater-mqtt-password on CT 110

region put #dk                  # Danish traffic is scoped to #dk; without it, scoped floods are dropped
region put #dk-fyn #dk
region put #dk-fyn-odense #dk-fyn
region default #dk              # own adverts scoped like the rest of the network
region save
```
`region` lists names without the `#` (`dk`), but keys are still derived from `#dk`
(RegionMap.cpp adds it back). Verified 2026-11-12: every scoped packet heard was `#dk`.

## Test status

- 2026-11-12: USB flash of CI build #3 OK, settings kept, panel auto-started.
  `ota check` OK over HTTPS; `ota update` #3 → #4 OK, panel back up.
- Pending: confirm #4 survives a smart-plug power cycle (proves the image was confirmed,
  not left on probation). If it comes back as #3, the confirm step is broken.
