# ha1fdan fork: headless repeater with web panel + self-hosted OTA

Fork of [agessaman/MeshCore](https://github.com/agessaman/MeshCore) (`observer-firmware`
branch, the MQTT Observer firmware). Work branch and fork default: `webconfig-autostart-lan`.

Built for `DK_5230_R01_Hunderup`, a Heltec V3 in the loft at 192.168.1.175 on a smart
plug, with no USB access and no companion node. Every change here exists so it can be
managed and updated without the ladder.

## What the fork adds

| Change | Where | Why |
|---|---|---|
| `WEBCONFIG_AUTOSTART_LAN` | `examples/simple_repeater/MyMesh.cpp` (loop) | Upstream only serves the web panel after `start webconfig` (serial or LoRa login). This starts it once WiFi connects, every boot, retrying every 30 s. A later `stop webconfig` sticks until reboot. |
| OTA rollback guard | `MyMesh.cpp` (`verifyRollbackLater`, loop) | Arduino marks a new OTA image valid at boot. Instead it stays on probation until the panel has been up 60 s. A crash before that, or 10 min without the panel, boots the previous image. |
| `OTA_CHECK_HTTPS` | `src/helpers/ESP32Board.cpp` | Upstream fetches the manifest over plain HTTP for `ota check` and the `ota update` pre-check. GitHub Pages redirects HTTP to HTTPS (301), so OTA never worked. With this flag the check uses the CA-verified TLS path. |
| Env `Heltec_v3_repeater_observer_mqtt_webui` | `variants/heltec_v3/platformio.ini` | Stock V3 observer env + `WEBCONFIG_AUTOSTART_LAN`. Stock envs are unchanged. |
| `OTA_UPDATE_ON_PRECHECK_CONNECT_FAIL` | `src/helpers/CommonCLI_Observer.cpp` | `ota update`'s pre-check fetches the manifest over TLS with MQTT up and fails intermittently on this no-PSRAM board (`manifest HTTP -1`). On a connect failure (negative code) it now proceeds with the deferred update, which pauses MQTT, re-checks the manifest itself, and resumes MQTT on any abort (up to date, partition change, download error). |
| `.github/workflows/ota-pages.yml` | | Builds every `*_webui` env and publishes the OTA channel to GitHub Pages. |
| `WEBCONFIG_RAW_TX` | `src/helpers/WebConfigRawTx.h`, `WebConfigServer.cpp`, `MyMesh.cpp` (`injectRawPacket`), `tools/webui_tx.py` | `POST /api/advert` + `POST /api/send` + `POST /api/channel` (+ `GET /api/tx/result?id=N`): transmit adverts / DMs for *other* identities, and channel (GRP_TXT) posts. Keys and crypto stay on the PC (`tools/webui_tx.py`); the node checks shape, verifies advert signatures, and sends through its normal paths (floods use `default_scope` = `#dk`). Auth: panel login or `X-Api-Token` (`txtoken new|off`). LAN mode only, 1 packet per 3 s. Send-only: replies/ACKs to those identities are just forwarded. |

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

## Raw TX (bot identities)

Built into OTA #5 and later (`WEBCONFIG_RAW_TX`). The script is `tools/webui_tx.py`, and its
docstring has more examples.

One-time setup, in the panel's CLI tab:
```
txtoken new      # OK - token <32 hex> (shown once): copy it
txtoken          # > set / > (not set)
txtoken off      # disables token auth (panel login still works)
```
The token lives in its own SPIFFS file (`/webui_tx_token`). It survives reboots and OTA
updates, and prefs are untouched. `txtoken new` replaces the old token.

On the PC:
```
export MESHCORE_TX_TOKEN=<token>
mkdir -p ~/.config/meshcore                      # keygen does not create the folder
tools/webui_tx.py keygen --out ~/.config/meshcore/bot.json --name "Hunderup Bot"   # once; keep out of git
tools/webui_tx.py advert --key ~/.config/meshcore/bot.json                 # FLOOD (default)
tools/webui_tx.py advert --key ~/.config/meshcore/bot.json --route ZERO-HOP
tools/webui_tx.py send --key ~/.config/meshcore/bot.json --to <64-hex pubkey> --text "hej"
tools/webui_tx.py channel --hashtag "#test" --name "Hunderup Bot" --text "hej"
read -s MESHCORE_CHANNEL_KEY && export MESHCORE_CHANNEL_KEY   # private channel secret, never on the command line
tools/webui_tx.py channel --name "Hunderup Bot" --text "hej"
```
- `channel --name` is only the display text before the message. Channel posts carry no
  identity, so they need no key file or advert, and anyone with the key can post as any name.
- A DM is accepted only after the recipient has heard this identity's advert, so advertise
  first. Replies and ACKs go to the bot identity, which nothing decrypts, so it is send-only.
- `advert --type/--lat/--lon/--name` change how the identity appears (chat/repeater/room/sensor,
  map position). The node rejects adverts with its own public key.

**How it looks on maps.** A node only adds its hash to a packet's path when it *forwards*
someone else's flood (`Mesh::routeRecvPacket`). A packet it originates leaves with an
empty path (`sendFlood` sets hop count 0). Bot packets are originated by the loft
repeater, so they leave with no hops, and the first repeater that re-floods them adds
its own hash. Maps that read "first hop = neighbor" therefore show *that* node as Hunderup
Bot's neighbor, not DK_5230_R01_Hunderup. This is expected: the bot looks like a separate
node standing in the loft, and that neighbor is one the repeater reaches directly (it
should appear among the repeater's own neighbors too, for the same reason). Adding the
repeater's hash to injected packets would make the map show the bot behind it, but every
bot path would then be one hop longer than the real radio route, so it's left as is.

Verified 2026-11-18: a channel post as "Hunderup Bot" arrived on a phone over the air
(scoped `#dk`, 2-byte path hashes). DMs (`advert` + `send`) have not been tested over the air yet.

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
set name DK_5230_R01_Hunderup
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
