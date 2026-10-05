/*
 * CRC-8 for the RC car protocol: reference implementation for the firmware.
 *
 * Variant: CRC-8/MAXIM-DOW (Dallas/Maxim 1-Wire)
 *   polynomial x^8 + x^5 + x^4 + 1 (0x31, 0x8C when reflected)
 *   init 0x00, reflected input and output, final XOR 0x00
 *   check("123456789") = 0xA1
 *
 * The CRC covers every byte of a packet except the sync word (0xAC 0x53)
 * and the CRC byte itself, i.e. LEN, SQN, ADDR and the payload.
 *
 * Known-good packets (SQN = 0, ADDR = 1):
 *   GET_TEL                    AC 53 04 00 01 04 AB
 *   SS_SA speed=50 angle=-20   AC 53 06 00 01 03 32 EC 2F
 *   reply ACK=0, no data       AC 53 04 00 01 00 CA
 *
 * The PC app (rc_controller/protocol/crc.py) is tested against the same
 * values. Build this file with -DCRC8_SELFTEST to run the checks on a PC:
 *   gcc -DCRC8_SELFTEST crc8.c -o crc8 && ./crc8
 */

#include <stddef.h>
#include <stdint.h>

uint8_t crc8_maxim(const uint8_t *data, size_t len)
{
    uint8_t crc = 0x00;
    while (len--) {
        crc ^= *data++;
        for (uint8_t bit = 0; bit < 8; bit++) {
            crc = (crc & 0x01) ? (uint8_t)((crc >> 1) ^ 0x8C) : (uint8_t)(crc >> 1);
        }
    }
    return crc;
}

/* Check a received packet: buf[0..1] = sync word, buf[2] = LEN, total length LEN + 3. */
int packet_crc_ok(const uint8_t *buf)
{
    uint8_t len = buf[2];
    return crc8_maxim(&buf[2], len) == buf[2 + len];
}

#ifdef CRC8_SELFTEST
#include <stdio.h>

static int expect(const char *name, uint8_t got, uint8_t want)
{
    printf("%-28s 0x%02X %s\n", name, got, got == want ? "ok" : "MISMATCH");
    return got == want ? 0 : 1;
}

int main(void)
{
    const uint8_t check[] = "123456789";
    const uint8_t get_tel[] = {0xAC, 0x53, 0x04, 0x00, 0x01, 0x04, 0xAB};
    const uint8_t ss_sa[] = {0xAC, 0x53, 0x06, 0x00, 0x01, 0x03, 0x32, 0xEC, 0x2F};
    const uint8_t ack[] = {0xAC, 0x53, 0x04, 0x00, 0x01, 0x00, 0xCA};
    int errors = 0;

    errors += expect("check(\"123456789\")", crc8_maxim(check, 9), 0xA1);
    errors += expect("GET_TEL", crc8_maxim(&get_tel[2], 4), 0xAB);
    errors += expect("SS_SA 50 -20", crc8_maxim(&ss_sa[2], 6), 0x2F);
    errors += expect("reply ACK 0", crc8_maxim(&ack[2], 4), 0xCA);
    errors += !packet_crc_ok(ss_sa);
    printf(errors ? "FAILED\n" : "all good\n");
    return errors;
}
#endif
