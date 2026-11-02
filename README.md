# Amnezia Split Tunneling Config Generator

ابزاری برای ساخت فایل قابل import در **AmneziaVPN** برای تنظیم **Split Tunneling**.

دو حالت دارد:

- **خودکار (پیش‌فرض):** لیست به‌روز IPv4 ایران را دانلود می‌کند و کانفیگ می‌سازد.
- **دستی:** یک فایل متنی از رنج‌های IPv4 (CIDR) می‌دهید و کانفیگ می‌سازد.

خروجی (`ip-list.json`):

```json
[
  { "hostname": "2.57.3.0/24", "ip": "" },
  { "hostname": "2.144.0.0/14", "ip": "" }
]
```

این همان فرمت رسمی Amnezia است که از مسیر
`Split Tunneling → ⋮ → Import` خوانده می‌شود.

## حالت خودکار

```bash
python3 amnezia_split.py
```

خروجی: `ip-list.json` در پوشهٔ جاری، به‌همراه `ip-list.json.meta.json`
(منبع، زمان ساخت، تعداد رنج‌ها و SHA256).

منبع پیش‌فرض:

| منبع | توضیح |
| --- | --- |
| `farshidmousavii/iran-ip-ranges` | هر ۶ ساعت از RIPE Stat به‌روز می‌شود؛ خروجی آن دقیقاً همان لیست مرجع است |
| `RIPE Stat (official)` | API رسمی RIPE؛ دادهٔ ثبت‌شدهٔ مرجع، بدون واسطه |
| `ipverse/rir-ip` | منبع جایگزین |
| `ipdeny.com` | منبع جایگزین دوم |

اگر منبع اول در دسترس نباشد، به‌ترتیب سراغ بعدی می‌رود. دانلود در
`~/.cache/amnezia-split/` کش می‌شود و تا ۶ ساعت دوباره دانلود نمی‌شود.

```bash
# نادیده گرفتن کش و دانلود مجدد
python3 amnezia_split.py --refresh

# فقط از کش (بدون اینترنت)
python3 amnezia_split.py --offline

# کشور دیگر
python3 amnezia_split.py --country de -o de.json

# منبع دلخواه (متن ساده یا JSON رسمی RIPE)
python3 amnezia_split.py --source https://example.com/list.txt
```

## بررسی صحت لیست

```bash
python3 amnezia_split.py --verify
```

این گزینه لیست را با دادهٔ رسمی RIPE مقایسه می‌کند:

```text
verify     : RIPE Stat registry (IR)
  registered addresses : 10,840,064
  list addresses       : 11,034,112
  registered missing   : 0 (0.00%)
  extra (announced)    : 194,048 (1.79%)
```

- `registered missing` باید نزدیک صفر باشد؛ یعنی هیچ رنج ثبت‌شدهٔ ایرانی جا نیفتاده.
- `extra` رنج‌هایی است که ASNهای ایرانی اعلام می‌کنند ولی در کشوری دیگر ثبت
  شده‌اند (رنج‌های منتقل‌شده یا LIR خارجی)؛ طبیعی و مفید است.

## چقدر این لیست معتبر است؟

بررسی‌های انجام‌شده روی نسخهٔ فعلی:

| بررسی | نتیجه |
| --- | --- |
| تازگی | هر ۶ ساعت؛ آخرین به‌روزرسانی همان روز |
| پوشش دادهٔ ثبت‌شدهٔ RIR | **۱۰۰٪** (۰ آدرس جاافتاده) |
| توافق با MaxMind GeoLite2 | **~۹۹.۲٪** از فضای آدرس |
| رنج‌های اضافه | ~۱.۸٪ (اعلام‌شده توسط ASNهای ایرانی، ثبت‌شده در کشور دیگر) |

منابع جایگزین و مقایسه:

| منبع | مزیت | محدودیت |
| --- | --- | --- |
| RIPE Stat (رسمی) | مرجع، بدون واسطه، همیشه در دسترس | فقط دادهٔ ثبت‌شده؛ ~۱.۸٪ رنج‌های منتقل‌شده را ندارد |
| MaxMind GeoLite2 | geolocation واقعی، به‌روزرسانی هفتگی | نیاز به کلید لایسنس؛ دقت نسخهٔ رایگان محدود |
| IP2Location LITE | رایگان، geolocation | به‌روزرسانی ماهانه |
| سرویس‌های تجاری (ipinfo، GeoIP2) | دقیق‌ترین | پولی |

برای حالت «آدرس‌های لیست بدون VPN بروند»، لیست فعلی انتخاب محافظه‌کارانه و
مناسبی است: فقط فضای آدرس ایران را خارج می‌کند و مثبت کاذب کمی دارد.


## حالت دستی

```bash
python3 amnezia_split.py ~/Documents/ipv4.txt
python3 amnezia_split.py ~/Documents/ipv4.txt -o iran.json
```

ورودی: هر خط یک CIDR یا IP، خطوط `#` به‌عنوان کامنت نادیده گرفته می‌شوند.

## زمان‌بندی به‌روزرسانی خودکار

### cron

```bash
crontab -e
```

```cron
# هر ۶ ساعت لیست را به‌روز کن
0 */6 * * * cd /home/mahdi/Projects/Amnezia-split-tunneling-config-generator && /usr/bin/python3 amnezia_split.py -o /home/mahdi/amnezia/ip-list.json -q >> /home/mahdi/amnezia/update.log 2>&1
```

### systemd timer

`~/.config/systemd/user/amnezia-split.service`:

```ini
[Unit]
Description=Update Amnezia split tunneling list

[Service]
Type=oneshot
WorkingDirectory=%h/Projects/Amnezia-split-tunneling-config-generator
ExecStart=/usr/bin/python3 amnezia_split.py -o %h/amnezia/ip-list.json -q
```

`~/.config/systemd/user/amnezia-split.timer`:

```ini
[Unit]
Description=Update Amnezia split tunneling list every 6 hours

[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now amnezia-split.timer
```

> فایل `ip-list.json` را بعد از هر به‌روزرسانی باید دوباره در برنامه import کنید
> (تغییر لیست فقط در حالت قطع اتصال ممکن است).

## مراحل import در AmneziaVPN

1. **قبل از اتصال** به `Settings → Split Tunneling` بروید.
2. حالت مسیردهی را انتخاب کنید:
   - `Addresses from the list should not be accessed via VPN` — آدرس‌های لیست
     بدون VPN می‌روند (مناسب لیست IPهای ایران). **پیش‌فرض ابزار.**
   - `Only the sites listed here will be accessed through the VPN` — فقط
     آدرس‌های لیست از VPN می‌روند.
3. روی `⋮` بزنید و `Import` را انتخاب کنید.
4. فایل `ip-list.json` را انتخاب و `Replace site list` را بزنید.
5. کلید Split Tunneling را روشن کنید و وصل شوید.

## گزینه‌ها

| گزینه | توضیح |
| --- | --- |
| `INPUT` | فایل ورودی؛ اگر حذف شود، لیست خودکار دانلود می‌شود |
| `-o, --output PATH` | مسیر فایل خروجی (پیش‌فرض `ip-list.json`) |
| `--mode {except,forward}` | حالت مسیردهی؛ در راهنمای چاپ‌شده و هشدارها اثر دارد |
| `--legacy` | فرمت سازگار با نسخه‌های باگ‌دار 5.0.0.x |
| `--collapse` | ادغام رنج‌های مجاور/هم‌پوشان |
| `--allowedips PATH` | نوشتن رنج‌ها به‌صورت یک خط جداشده با کاما برای `AllowedIPs` |
| `--verify` | مقایسه با دادهٔ رسمی RIPE و چاپ آمار پوشش |
| `-q, --quiet` | فقط چاپ مسیر خروجی (مناسب cron) |

گزینه‌های حالت خودکار:

| گزینه | توضیح |
| --- | --- |
| `--country CC` | کد کشور (پیش‌فرض `IR`) |
| `--source URL` | منبع دلخواه؛ قابل تکرار |
| `--max-age SECONDS` | حداکثر عمر کش (پیش‌فرض ۲۱۶۰۰ = ۶ ساعت) |
| `--refresh` | نادیده گرفتن کش |
| `--offline` | فقط کش، بدون دانلود |
| `--cache-dir PATH` | مسیر کش (پیش‌فرض `~/.cache/amnezia-split`) |
| `--timeout SECONDS` | مهلت شبکه (پیش‌فرض ۳۰) |
| `--min-networks N` | حداقل تعداد رنج برای معتبر دانستن دانلود (پیش‌فرض ۱۰) |

## نکات مهم

- **نسخهٔ کلاینت:** در Amnezia 5.0.0.x باگی وجود داشت که پیشوند CIDR را هنگام
  import حذف می‌کرد (`1.192.0.0/10` → `1.192.0.0`) و رنج‌ها به `/32` تبدیل
  می‌شدند. این باگ در **5.0.1.5** رفع شده است. اگر مجبور به استفاده از نسخهٔ
  قدیمی هستید، از `--legacy` استفاده کنید.
- **فقط IPv4:** Amnezia از IPv6 در split tunneling پشتیبانی نمی‌کند؛ خطوط IPv6
  با هشدار نادیده گرفته می‌شوند.
- **حالت Free:** در اتصال Amnezia Free، split tunneling بر اساس IP در دسترس نیست.
- **خطای «Default server does not support split tunneling function»:** مقدار
  `AllowedIPs` در کانفیگ سرور باید دقیقاً `0.0.0.0/0, ::/0` باشد (با فاصله بعد
  از کاما). اگر `0.0.0.0/0,::/0` باشد، کلاینت آن را تشخیص نمی‌دهد.
- **حجم لیست:** لیست‌های خیلی بزرگ می‌توانند رابط کاربری Amnezia را کند کنند.
  با `--collapse` تعداد entryها را کم کنید.
- **دقت داده:** لیست بر پایهٔ داده‌های RIPE است و تضمین کامل geolocation نیست.

## تست

```bash
python3 -m unittest discover -s tests -v
```

نیازی به کتابخانهٔ بیرونی نیست؛ فقط Python 3.9+.
