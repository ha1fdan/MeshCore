#pragma once

// Fork-owned pure spec for the web panel's raw-TX endpoints (WEBCONFIG_RAW_TX):
//
//   POST /api/advert  {"advert":"<hex>", "route":"FLOOD"|"ZERO-HOP"}
//   POST /api/send    {"payload":"<hex>", "path":"<hex>", "path_hash_size":1..3}
//   GET  /api/tx/result?id=N
//
// The client (tools/webui_tx.py) holds the private keys and does all signing
// and encryption. The node only checks the shape of the bytes, wraps them in a
// packet header and transmits them through its normal send paths (so flood
// region scoping, path-hash mode and duplicate suppression all still apply).
// Nothing here touches Arduino, the radio or crypto, so it is host-tested by
// test/test_webconfig_raw_tx.

#include <stdint.h>
#include <stddef.h>
#include <string.h>
#include <MeshCore.h>

namespace WebConfigRawTx {

enum class Kind : uint8_t {
  AdvertFlood = 0,
  AdvertZeroHop,
  TxtFlood,
  TxtDirect,
};

struct Request {
  Kind kind = Kind::AdvertFlood;
  uint8_t payload[MAX_PACKET_PAYLOAD];
  uint8_t payload_len = 0;
  uint8_t path[MAX_PATH_SIZE];
  uint8_t path_len = 0;   // encoded MeshCore form: ((hash_size-1) << 6) | hop_count. TxtDirect only.
};

// Advert payload: pub_key(32) + timestamp(4) + signature(64) + app_data(1..32).
static const size_t kAdvertHeaderLen = PUB_KEY_SIZE + 4 + SIGNATURE_SIZE;
static const size_t kAdvertMinLen = kAdvertHeaderLen + 1;   // app_data flags byte is mandatory
static const size_t kAdvertMaxLen = kAdvertHeaderLen + MAX_ADVERT_DATA_SIZE;

// TXT_MSG datagram payload: dest_hash(1) + src_hash(1) + MAC(2) + ciphertext(n*16).
static const size_t kTxtHeaderLen = 2 * PATH_HASH_SIZE + CIPHER_MAC_SIZE;
static const size_t kTxtMinLen = kTxtHeaderLen + CIPHER_BLOCK_SIZE;

// Default spacing between accepted transmissions. One full-size SF8/62.5 kHz
// packet is roughly 0.5-1 s of airtime; 3 s keeps a runaway client well under
// the 10% duty cycle of the 869.4-869.65 MHz sub-band.
#ifndef WEBCONFIG_RAW_TX_MIN_INTERVAL_MS
  #define WEBCONFIG_RAW_TX_MIN_INTERVAL_MS 3000UL
#endif

inline int hexNibble(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// Strict hex decode: even length, hex digits only, at most `max` bytes.
// An empty string decodes to zero bytes.
inline bool parseHex(const char* hex, uint8_t* out, size_t max, size_t* out_len) {
  if (!hex || !out || !out_len) return false;
  size_t n = strlen(hex);
  if (n % 2 != 0 || n / 2 > max) return false;
  for (size_t i = 0; i < n / 2; i++) {
    int hi = hexNibble(hex[2 * i]);
    int lo = hexNibble(hex[2 * i + 1]);
    if (hi < 0 || lo < 0) return false;
    out[i] = (uint8_t)((hi << 4) | lo);
  }
  *out_len = n / 2;
  return true;
}

// "FLOOD" / "ZERO-HOP", case-insensitive; "_" and "zerohop" also accepted.
// NULL or empty means FLOOD.
inline bool parseAdvertRoute(const char* s, bool* zero_hop) {
  if (!zero_hop) return false;
  if (!s || !*s) { *zero_hop = false; return true; }
  char norm[16];
  size_t j = 0;
  for (size_t i = 0; s[i]; i++) {
    char c = s[i];
    if (c == '-' || c == '_') continue;
    if (c >= 'A' && c <= 'Z') c = (char)(c - 'A' + 'a');
    if (j >= sizeof(norm) - 1) return false;
    norm[j++] = c;
  }
  norm[j] = 0;
  if (strcmp(norm, "flood") == 0) { *zero_hop = false; return true; }
  if (strcmp(norm, "zerohop") == 0) { *zero_hop = true; return true; }
  return false;
}

// Returns NULL when valid, else a short reason for the 400 response.
inline const char* checkAdvertPayload(const uint8_t* p, size_t len, const uint8_t* own_pub_key) {
  if (len < kAdvertMinLen) return "advert too short";
  if (len > kAdvertMaxLen) return "advert too long";
  if (own_pub_key && memcmp(p, own_pub_key, PUB_KEY_SIZE) == 0) return "that is this node's own key";
  return NULL;
}

inline const char* checkTxtPayload(size_t len) {
  if (len < kTxtMinLen) return "payload too short";
  if (len > MAX_PACKET_PAYLOAD) return "payload too long";
  if ((len - kTxtHeaderLen) % CIPHER_BLOCK_SIZE != 0) return "ciphertext not block aligned";
  return NULL;
}

// Encode a direct path of `byte_len` raw bytes split into `hash_size`-byte hops.
inline const char* encodePath(size_t byte_len, int hash_size, uint8_t* path_len_out) {
  if (hash_size < 1 || hash_size > 3) return "path_hash_size must be 1-3";
  if (byte_len == 0) return "path is empty";
  if (byte_len % (size_t)hash_size != 0) return "path length not a multiple of path_hash_size";
  size_t hops = byte_len / (size_t)hash_size;
  if (hops > 63 || byte_len > MAX_PATH_SIZE) return "path too long";
  *path_len_out = (uint8_t)(((hash_size - 1) << 6) | hops);
  return NULL;
}

// API token for the TX endpoints (X-Api-Token header): 32 hex chars, created
// on the node with `txtoken new`, kept in its own SPIFFS file (prefs untouched).
static const size_t kTokenHexLen = 32;

inline bool isValidToken(const char* s) {
  if (!s) return false;
  for (size_t i = 0; i < kTokenHexLen; i++) {
    if (hexNibble(s[i]) < 0) return false;   // also stops at a short string's NUL
  }
  return s[kTokenHexLen] == 0;
}

// Constant-time over the token length. An unset (empty) expected token never matches.
inline bool tokenEquals(const char* got, const char* expected) {
  if (!got || !expected || !isValidToken(expected)) return false;
  size_t glen = strnlen(got, kTokenHexLen + 1);
  uint8_t diff = (glen == kTokenHexLen) ? 0 : 1;
  for (size_t i = 0; i < kTokenHexLen; i++) {
    char a = (i < glen) ? got[i] : 0;
    char b = expected[i];
    if (a >= 'A' && a <= 'F') a = (char)(a - 'A' + 'a');
    if (b >= 'A' && b <= 'F') b = (char)(b - 'A' + 'a');
    diff |= (uint8_t)(a ^ b);
  }
  return diff == 0;
}

// millis()-rollover-safe spacing check. last_ms == 0 means "never sent".
inline bool rateOk(uint32_t last_ms, uint32_t now_ms, uint32_t min_interval_ms) {
  if (last_ms == 0) return true;
  return (uint32_t)(now_ms - last_ms) >= min_interval_ms;
}

}  // namespace WebConfigRawTx
