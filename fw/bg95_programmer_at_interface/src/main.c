/*
 * Copyright (c) 2026 Nordic Semiconductor ASA
 *
 * SPDX-License-Identifier: LicenseRef-Nordic-5-Clause
 */

/*
 * Plain AT interface to an nRF9151 running Serial Modem on the
 * nRF9151-MK-BG95 interposer. Whatever is typed on UART20 / VCOM1 is sent to
 * the modem as an AT command and the reply is printed below it.
 */

#include <errno.h>
#include <ctype.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/ring_buffer.h>

#define RING_SIZE     1024
#define LINE_SIZE     256
#define RSP_BUF_SIZE  1024
#define AT_TERMINATOR '\r'
#define AT_TIMEOUT_MS 5000
#define FLUSH_TIMEOUT_MS 100
#define PROMPT        "~$ "

#define KEY_BS  0x08
#define KEY_DEL 0x7f

struct rx_channel {
	const struct device *dev;
	struct ring_buf ring;
	uint8_t mem[RING_SIZE];
	struct k_sem sem;
};

static struct rx_channel con; /* UART20 / VCOM1: user terminal */
static struct rx_channel sm;  /* UART21: nRF9151 Serial Modem */

/* Woken by either UART so the idle loop can react to modem data (e.g. "Ready"). */
static K_SEM_DEFINE(wake_sem, 0, 1);

static char line[LINE_SIZE]; /* line being typed */
static size_t line_len;
static char urc[LINE_SIZE]; /* unsolicited modem line being assembled */
static size_t urc_len;

static void uart_cb(const struct device *dev, void *user_data)
{
	struct rx_channel *ch = user_data;
	uint8_t buf[32];

	uart_irq_update(dev);

	while (uart_irq_rx_ready(dev)) {
		int len = uart_fifo_read(dev, buf, sizeof(buf));

		if (len <= 0) {
			break;
		}
		/* Data is dropped if the ring buffer is full. */
		ring_buf_put(&ch->ring, buf, len);
		k_sem_give(&ch->sem);
		k_sem_give(&wake_sem);
	}
}

static int channel_init(struct rx_channel *ch, const struct device *dev)
{
	int err;

	if (!device_is_ready(dev)) {
		return -ENODEV;
	}
	ch->dev = dev;
	ring_buf_init(&ch->ring, sizeof(ch->mem), ch->mem);
	k_sem_init(&ch->sem, 0, 1);

	err = uart_irq_callback_user_data_set(dev, uart_cb, ch);
	if (err) {
		return err;
	}
	uart_irq_rx_enable(dev);

	return 0;
}

static void out_char(const struct device *dev, char c)
{
	uart_poll_out(dev, c);
}

/* Write to the terminal, converting "\n" to "\r\n". */
static void con_puts(const char *str)
{
	for (; *str != '\0'; str++) {
		if (*str == '\n') {
			out_char(con.dev, '\r');
		}
		out_char(con.dev, *str);
	}
}

static bool rsp_is_final(const char *rsp, size_t len)
{
	if (len >= 4 && strcmp(rsp + len - 4, "OK\r\n") == 0) {
		return true;
	}

	return strstr(rsp, "ERROR") != NULL && len >= 2 && strcmp(rsp + len - 2, "\r\n") == 0;
}

/* Print the buffer line by line, skipping empty lines. */
static void print_lines(char *rsp)
{
	char *save = NULL;

	for (char *line = strtok_r(rsp, "\r\n", &save); line != NULL;
	     line = strtok_r(NULL, "\r\n", &save)) {
		con_puts(line);
		con_puts("\n");
	}
}

static void run_at(const char *cmd)
{
	static char rsp[RSP_BUF_SIZE];
	size_t len;
	int64_t deadline;

	/* Show anything received since the last command (notifications). */
	len = ring_buf_get(&sm.ring, (uint8_t *)rsp, sizeof(rsp) - 1);
	rsp[len] = '\0';
	if (len > 0) {
		print_lines(rsp);
	}
	k_sem_reset(&sm.sem);

	for (; *cmd != '\0'; cmd++) {
		out_char(sm.dev, *cmd);
	}
	out_char(sm.dev, AT_TERMINATOR);

	len = 0;
	rsp[0] = '\0';
	deadline = k_uptime_get() + AT_TIMEOUT_MS;

	while (!rsp_is_final(rsp, len) && len < sizeof(rsp) - 1) {
		int64_t left = deadline - k_uptime_get();

		if (left <= 0 || k_sem_take(&sm.sem, K_MSEC(left)) != 0) {
			if (len > 0) {
				print_lines(rsp);
				con_puts("Timeout, no final result\n");
			} else {
				con_puts("No response from nRF9151\n");
			}
			return;
		}
		len += ring_buf_get(&sm.ring, (uint8_t *)&rsp[len], sizeof(rsp) - 1 - len);
		rsp[len] = '\0';
	}

	print_lines(rsp);
}

/* Print a modem line that arrived while idle, then redraw the prompt and typed text. */
static void urc_emit(void)
{
	if (urc_len == 0) {
		return;
	}
	urc[urc_len] = '\0';
	urc_len = 0;

	con_puts("\r\x1b[K");
	con_puts(urc);
	con_puts("\n" PROMPT);
	for (size_t i = 0; i < line_len; i++) {
		out_char(con.dev, line[i]);
	}
}

static bool sm_service(void)
{
	uint8_t c;
	bool got = false;

	while (ring_buf_get(&sm.ring, &c, 1) == 1) {
		got = true;
		if (c == '\r' || c == '\n') {
			urc_emit();
			continue;
		}
		if (urc_len >= sizeof(urc) - 1) {
			urc_emit();
		}
		urc[urc_len++] = isprint(c) ? (char)c : '.';
	}

	return got;
}

static void handle_char(uint8_t c)
{
	static bool prev_cr;

	if (c == '\r' || c == '\n') {
		if (c == '\n' && prev_cr) {
			prev_cr = false;
			return;
		}
		prev_cr = (c == '\r');

		con_puts("\n");
		if (line_len > 0) {
			line[line_len] = '\0';
			run_at(line);
			con_puts("\n");
		}
		line_len = 0;
		con_puts(PROMPT);
		return;
	}
	prev_cr = false;

	if (c == KEY_BS || c == KEY_DEL) {
		if (line_len > 0) {
			line_len--;
			con_puts("\b \b");
		}
	} else if (isprint(c) && line_len < LINE_SIZE - 1) {
		line[line_len++] = (char)c;
		out_char(con.dev, c);
	}
}

int main(void)
{
	if (channel_init(&con, DEVICE_DT_GET(DT_ALIAS(at_uart))) ||
	    channel_init(&sm, DEVICE_DT_GET(DT_ALIAS(sm_uart)))) {
		return -ENODEV;
	}

	con_puts(PROMPT);

	while (true) {
		uint8_t c;

		/* Show modem output as soon as it arrives, e.g. "Ready" after power-up. */
		if (sm_service()) {
			continue;
		}
		if (ring_buf_get(&con.ring, &c, 1) == 1) {
			handle_char(c);
			continue;
		}
		if (k_sem_take(&wake_sem, urc_len > 0 ? K_MSEC(FLUSH_TIMEOUT_MS) : K_FOREVER) != 0) {
			/* Idle: show a partial line such as a prompt without newline. */
			urc_emit();
		}
	}

	return 0;
}
