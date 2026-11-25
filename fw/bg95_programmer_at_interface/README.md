# BG95 Programmer AT Interface

Simple AT command shell on an nRF54LM20 DK for an nRF9151 running Serial
Modem (built with ``interposer-nrf9151-mk-bg95.overlay``).

Wiring (UART0 on the nRF9151, 115200 8N1, no flow control)
**********************************************************

|nRF54LM20 DK      | nRF9151 (interposer) |
|------------------|----------------------|
|P1.03 (UART21 TX) | P0.14 (RX)           |
|P1.04 (UART21 RX) | P0.15 (TX)           |
| 3.0 V - 5.5 V    | VDD (VBAT_RF + VBAT_BB on the jig) |
|GND               | GND                  |

> [!NOTE]
> VDD_IO must be set to 1.8 V on the nRF54LM20 DK.
> The supply voltage must be between 3.0 V and 5.5 V.

Usage
*****

Open VCOM1 at 115200 8N1 and type AT commands directly::

```shell
   ~$ AT
   OK

   ~$ AT+CGMR
   +CGMR: ...
   OK
````

* Each line is sent to the nRF9151 followed by CR, and the reply is printed
  until ``OK``, ``ERROR`` or ``+CME/+CMS ERROR`` (5 s timeout).
* Text is sent unchanged.
* Backspace edits the line. Data received between commands (notifications)
  is printed as soon as it arrives, for example the ``Ready`` line the
  nRF9151 sends at power-up. It is shown above the prompt, which is redrawn
  together with the text you were typing.
