# فهرست پژوهش‌های این پروژه

همهٔ خانواده‌ها نگه داشته شده‌اند؛ نتیجهٔ مثبت انتخاب‌شده جای نتایج ناموفق را نگرفته است. جزئیات مفروضات، بازه‌ها، هزینه و مقایسه با خرید و نگه‌داری در هر گزارش موجود است.

| موضوع | گزارش | کد اصلی |
|---|---|---|
| نمونهٔ اولیه و روند ۷۲ساعته | RESULTS.md، TREND_72H_RESULT.md | src/wallex_agent/engine.py |
| BTC/PUMP در مقیاس ۵ و ۱۵دقیقه | INTRADAY_BTC_PUMP.md | experiments/intraday_two_coins.py |
| همان دو نماد ساعتی و تمام سرمایه | HOURLY_BTC_PUMP_ALL_IN.md | experiments/hourly_two_coins.py |
| تقدم بایننس/والکس | BINANCE_WALLEX_LEADLAG.md | experiments/cross_venue_leadlag.py |
| پیش‌بینی حرکت باقی‌مانده | REMAINING_MOVE_MODEL.md | experiments/remaining_move_model.py |
| مدل دو دقیقه‌ای و هزینهٔ اجرا | TWO_MINUTE_EXECUTION.md | experiments/execution_aligned_two_minute_model.py |
| لاک‌پشت ساعتی | TURTLE_HOURLY_BTC_PUMP.md | experiments/turtle_hourly.py |
| همهٔ بازارها و خانواده‌های استراتژی | RESEARCH30_ALL_METHODS_FA.md | experiments/research30.py و research30_controls.py |
| آزمون ۶۰/۹۰روزهٔ نسخهٔ منجمد | RELATIVE_STRENGTH_60_90_FA.md | experiments/research_long_horizons.py |
| هدف سود تجدیدشونده و سیاست افت حساب | MONTHLY_GOALS_FA.md | experiments/research_monthly_goals.py |
| مشاهدهٔ نرخ واقعی حساب | VERIFIED_ACCOUNT_FEES.md | config/package-183-fees-observed.json |

فایل‌های عددی کامل در پوشهٔ `results/`، داده‌های عمومی منجمد در `data/` و تست‌های پژوهش در `experiments/test_*.py` قرار دارند. بسته‌های ZIP پژوهش نسخه‌های تاریخی مستقل‌اند؛ برای کار با نسخهٔ فعلی، سورس ریشه را با فایل‌های قدیمی داخل ZIP جایگزین نکنید.
