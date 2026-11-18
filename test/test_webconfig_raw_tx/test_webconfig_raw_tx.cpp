#include <gtest/gtest.h>
#include "helpers/WebConfigRawTx.h"

using namespace WebConfigRawTx;

TEST(RawTxHex, DecodesBothCases) {
  uint8_t out[4]; size_t n = 99;
  ASSERT_TRUE(parseHex("0aFf10", out, sizeof(out), &n));
  EXPECT_EQ(n, 3u);
  EXPECT_EQ(out[0], 0x0a); EXPECT_EQ(out[1], 0xff); EXPECT_EQ(out[2], 0x10);
}

TEST(RawTxHex, EmptyIsZeroBytes) {
  uint8_t out[1]; size_t n = 99;
  ASSERT_TRUE(parseHex("", out, sizeof(out), &n));
  EXPECT_EQ(n, 0u);
}

TEST(RawTxHex, RejectsOddBadAndOversize) {
  uint8_t out[2]; size_t n;
  EXPECT_FALSE(parseHex("abc", out, sizeof(out), &n));
  EXPECT_FALSE(parseHex("zz", out, sizeof(out), &n));
  EXPECT_FALSE(parseHex("0x12", out, sizeof(out), &n));
  EXPECT_FALSE(parseHex("aabbcc", out, sizeof(out), &n));   // 3 bytes > max 2
  EXPECT_TRUE(parseHex("aabb", out, sizeof(out), &n));      // exactly max
  EXPECT_FALSE(parseHex(nullptr, out, sizeof(out), &n));
}

TEST(RawTxRoute, DefaultsToFlood) {
  bool zh = true;
  ASSERT_TRUE(parseAdvertRoute(nullptr, &zh)); EXPECT_FALSE(zh);
  zh = true;
  ASSERT_TRUE(parseAdvertRoute("", &zh)); EXPECT_FALSE(zh);
}

TEST(RawTxRoute, AcceptsSpellings) {
  bool zh;
  ASSERT_TRUE(parseAdvertRoute("FLOOD", &zh)); EXPECT_FALSE(zh);
  ASSERT_TRUE(parseAdvertRoute("flood", &zh)); EXPECT_FALSE(zh);
  ASSERT_TRUE(parseAdvertRoute("ZERO-HOP", &zh)); EXPECT_TRUE(zh);
  ASSERT_TRUE(parseAdvertRoute("zero_hop", &zh)); EXPECT_TRUE(zh);
  ASSERT_TRUE(parseAdvertRoute("ZeroHop", &zh)); EXPECT_TRUE(zh);
}

TEST(RawTxRoute, RejectsOthers) {
  bool zh;
  EXPECT_FALSE(parseAdvertRoute("direct", &zh));
  EXPECT_FALSE(parseAdvertRoute("zero", &zh));
  EXPECT_FALSE(parseAdvertRoute("floodfloodfloodflood", &zh));
}

TEST(RawTxAdvert, LengthBounds) {
  uint8_t p[kAdvertMaxLen + 1] = {0};
  uint8_t own[PUB_KEY_SIZE]; memset(own, 0xAA, sizeof(own));
  EXPECT_NE(checkAdvertPayload(p, kAdvertHeaderLen, own), nullptr);     // no app_data
  EXPECT_EQ(checkAdvertPayload(p, kAdvertMinLen, own), nullptr);
  EXPECT_EQ(checkAdvertPayload(p, kAdvertMaxLen, own), nullptr);
  EXPECT_NE(checkAdvertPayload(p, kAdvertMaxLen + 1, own), nullptr);
}

TEST(RawTxAdvert, RejectsNodesOwnKey) {
  uint8_t own[PUB_KEY_SIZE]; memset(own, 0xAA, sizeof(own));
  uint8_t p[kAdvertMinLen]; memset(p, 0, sizeof(p)); memcpy(p, own, PUB_KEY_SIZE);
  EXPECT_NE(checkAdvertPayload(p, sizeof(p), own), nullptr);
  EXPECT_EQ(checkAdvertPayload(p, sizeof(p), nullptr), nullptr);
}

TEST(RawTxTxt, LengthAndAlignment) {
  EXPECT_NE(checkTxtPayload(kTxtHeaderLen), nullptr);
  EXPECT_EQ(checkTxtPayload(kTxtHeaderLen + 16), nullptr);
  EXPECT_NE(checkTxtPayload(kTxtHeaderLen + 17), nullptr);
  EXPECT_EQ(checkTxtPayload(kTxtHeaderLen + 176), nullptr);   // 180, fits 184
  EXPECT_NE(checkTxtPayload(kTxtHeaderLen + 192), nullptr);   // over MAX_PACKET_PAYLOAD
}

TEST(RawTxPath, EncodesSizeAndCount) {
  uint8_t enc = 0;
  ASSERT_EQ(encodePath(3, 1, &enc), nullptr); EXPECT_EQ(enc, 3);
  ASSERT_EQ(encodePath(4, 2, &enc), nullptr); EXPECT_EQ(enc, (1 << 6) | 2);
  ASSERT_EQ(encodePath(63, 3, &enc), nullptr); EXPECT_EQ(enc, (2 << 6) | 21);
  ASSERT_EQ(encodePath(63, 1, &enc), nullptr); EXPECT_EQ(enc, 63);
}

TEST(RawTxPath, Rejects) {
  uint8_t enc;
  EXPECT_NE(encodePath(0, 1, &enc), nullptr);
  EXPECT_NE(encodePath(3, 2, &enc), nullptr);   // not a multiple
  EXPECT_NE(encodePath(4, 4, &enc), nullptr);   // size 4 reserved
  EXPECT_NE(encodePath(4, 0, &enc), nullptr);
  EXPECT_NE(encodePath(64, 1, &enc), nullptr);  // 64 hops > 63
}

TEST(RawTxRate, SpacingAndRollover) {
  EXPECT_TRUE(rateOk(0, 5, 3000));
  EXPECT_FALSE(rateOk(1000, 3999, 3000));
  EXPECT_TRUE(rateOk(1000, 4000, 3000));
  EXPECT_FALSE(rateOk(0xFFFFFF00u, 0x00000100u, 3000));   // 512 ms across rollover
  EXPECT_TRUE(rateOk(0xFFFFFF00u, 0x00000C00u, 3000));    // 3328 ms across rollover
}

TEST(RawTxToken, Validity) {
  EXPECT_TRUE(isValidToken("0123456789abcdef0123456789ABCDEF"));
  EXPECT_FALSE(isValidToken("0123456789abcdef0123456789abcde"));    // 31
  EXPECT_FALSE(isValidToken("0123456789abcdef0123456789abcdef0"));  // 33
  EXPECT_FALSE(isValidToken("0123456789abcdef0123456789abcdeg"));
  EXPECT_FALSE(isValidToken(""));
  EXPECT_FALSE(isValidToken(nullptr));
}

TEST(RawTxToken, Equality) {
  const char* t = "0123456789ABCDEF0123456789ABCDEF";
  EXPECT_TRUE(tokenEquals("0123456789ABCDEF0123456789ABCDEF", t));
  EXPECT_TRUE(tokenEquals("0123456789abcdef0123456789abcdef", t));   // case-insensitive
  EXPECT_FALSE(tokenEquals("0123456789ABCDEF0123456789ABCDE0", t));
  EXPECT_FALSE(tokenEquals("0123456789ABCDEF0123456789ABCDE", t));   // prefix
  EXPECT_FALSE(tokenEquals("0123456789ABCDEF0123456789ABCDEF00", t)); // longer
  EXPECT_FALSE(tokenEquals("", t));
  EXPECT_FALSE(tokenEquals(nullptr, t));
}

TEST(RawTxToken, UnsetNeverMatches) {
  EXPECT_FALSE(tokenEquals("", ""));
  EXPECT_FALSE(tokenEquals("0123456789abcdef0123456789abcdef", ""));
  EXPECT_FALSE(tokenEquals("anything", nullptr));
}

int main(int argc, char** argv) {
  ::testing::InitGoogleTest(&argc, argv);
  return RUN_ALL_TESTS();
}
