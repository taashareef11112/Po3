# Po3 - KuCoin Low Cap Liquidity Hunter

بوت بايثون لرصد العملات منخفضة الماركت كاب (5M$ وأقل) على KuCoin مع تنبيهات تيليجرام عربية.

## الميزات
- خوارزمية شرطية ذكية **بدون Scores**.
- فلترة منخفضة الماركت كاب عبر CoinGecko Market Cap.
- تقليل الإشارات العشوائية عبر:
  - حد أقصى للسبريد.
  - حد أدنى لتدفق USDT.
  - قفزة حجم + حركة سعر + شرط زخم.
  - Cooldown لمنع التكرار.

## التشغيل
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python kucoin_liquidity_hunter.py
```

## ملاحظات مهمة
- هذا البوت للتنبيه فقط وليس توصية مالية.
- ينصح بإضافة فلاتر إضافية مثل تحليل دفتر الأوامر والشموع قبل التنفيذ الآلي.
