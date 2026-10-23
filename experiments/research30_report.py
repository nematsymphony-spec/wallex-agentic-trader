"""Persian report and real current-book screen; all claims tied to saved results."""
import csv
import hashlib
import json
import statistics as st
from datetime import datetime,timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from research30 import OUT, ROOT, START, END, TRAIN, percentile, valid, dump

TZ=ZoneInfo('Asia/Tehran')
def local(ts):return datetime.fromtimestamp(ts,TZ).strftime('%Y-%m-%d %H:%M')
def fmt(v,d=2):return f'{v:,.{d}f}' if v is not None else 'ناموجود'


def current_screen():
    meta=json.loads((ROOT/'universe.json').read_text())['markets']
    books=json.loads((ROOT/'book-metrics.json').read_text())
    screens={};most=[]
    for capital in (5_000_000,10_000_000,50_000_000,100_000_000):
        rows=[]
        for m in meta:
            s=m['symbol'];ds=json.loads((ROOT/f'{s}-daily.json').read_text())
            bs=[ds.get(str(t)) for t in range(END-7*86400,END,86400)]
            if not all(valid(b,True) for b in bs):continue
            ranges=[100*(b['h']/b['l']-1) for b in bs]
            if capital==5_000_000:most.append({'symbol':s,'mean7':st.mean(ranges),'mean3':st.mean(ranges[-3:])})
            xs=books.get(s,{}).get(str(capital),[])
            if len(xs)!=3 or not all(x['valid'] for x in xs):continue
            costs=[x['roundtrip_cost_pct'] for x in xs]
            rows.append({'symbol':s,'capital_tmn':capital,'mean7':st.mean(ranges),'mean3':st.mean(ranges[-3:]),
                         'median7':st.median(ranges),'valid_days':7,'volatility_increasing':st.mean(ranges[-3:])>st.mean(ranges),
                         'spread_pct':st.mean(x['spread_pct'] for x in xs),'roundtrip_cost_pct':st.mean(costs),
                         'cost_min_pct':min(costs),'cost_max_pct':max(costs),
                         'buy_vwap':st.mean(x['buy_vwap'] for x in xs),'sell_vwap':st.mean(x['sell_vwap'] for x in xs),
                         'quote_volume_24h_tmn':float(m.get('quote_volume_24h',0)),
                         'fee_each_side_pct':.3,'risk_flag':m.get('flags',[]),
                         'costly_relative_to_range':st.mean(costs)>.25*st.mean(ranges[-3:]),
                         'unstable_three_snapshots':max(costs)-min(costs)>.5,
                         'snapshot_times_utc':[x['at'] for x in xs]})
        if rows:
            p7=percentile([r['mean7'] for r in rows]);p3=percentile([r['mean3'] for r in rows]);pc=percentile([-r['roundtrip_cost_pct'] for r in rows]);pv=percentile([r['quote_volume_24h_tmn'] for r in rows])
            for j,r in enumerate(rows):r['score']=float(.4*p7[j]+.3*p3[j]+.2*pc[j]+.1*pv[j])
            rows.sort(key=lambda r:(-r['score'],r['symbol']))
            for rank,r in enumerate(rows,1):r['rank']=rank
        screens[str(capital)]=rows
    out={'most_volatile_allowed':max(most,key=lambda r:r['mean7']),
         'method':'Requested 40/30/20/10 full current screen; all 3 book snapshots must have enough displayed depth',
         'not_historical':'These current books are NOT substituted into prior-month fills or rankings',
         'screens':screens}
    dump(OUT/'current-full-screen.json',out)
    with (OUT/'current-full-screen.csv').open('w') as f:
        keys=[k for k in screens['5000000'][0] if k not in ('snapshot_times_utc','risk_flag')]
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        for rr in screens.values():
            for r in rr:w.writerow({k:r[k]for k in keys})
    return out


def main():
    current=current_screen();summary=json.loads((OUT/'summary.json').read_text());micro=json.loads((OUT/'minute-replay.json').read_text());control=json.loads((OUT/'controls.json').read_text())
    names={'relative_strength':'قدرت نسبی','range_reversion':'بازگشت به میانگین در بازار خنثی','compression_breakout':'شکست پس از فشردگی نوسان','exit_comparison':'مقایسه خروج‌های ثابت و ATR','ai_filter':'فیلتر AI آموزش‌دیده','volatility_control':'مبنای انتخاب نوسان'}
    rows=json.loads((OUT/'all-scenarios.json').read_text())
    best=micro['runs']['relative_strength-007-goal5-OLHC'];cap3=micro['runs']['relative_strength-007-goal3-OLHC'];trade=best['trade_log'][0]
    lines=['# آزمون ۳۰روزهٔ روش‌های انتخاب نماد و معاملهٔ والکس','',
           f'تاریخ گزارش: {local(datetime.now(timezone.utc).timestamp())} تهران. هیچ سفارش واقعی ثبت نشده است.','',
           '## نتیجهٔ اصلی','',
           f"هدف عددی در این شبیه‌سازی تاریخی محقق شد: نسخهٔ قدرت نسبی با توقف حساب پس از سود خالص ۳٪، **{cap3['return_pct']:+.3f}٪** و با هدف ۵٪، **{best['return_pct']:+.3f}٪** داد. هر دو فقط BCH را معامله کردند و سپس تا پایان ماه نقد ماندند. این نتیجه بازده ماهانهٔ تکرارپذیر، برتری بر خرید و نگهداری یا اجرای واقعی در حساب اعتباری را اثبات نمی‌کند.", '',
           '## دوره، سرمایه و هزینه','',
           f'- دوره هدف: **{local(START)} تا {local(END)} تهران**؛ انتها مستثنا، ۳۰ روز کامل UTC. روز جاری ناقص کنار گذاشته شد.',
           f'- ماه قبل برای مقایسه و انتخاب نسخه: **{local(TRAIN)} تا {local(START)}**. دادهٔ پیش از آن برای گرم‌کردن ویژگی‌ها استفاده شد.',
           '- سرمایه اولیه هر اجرا ۵٬۰۰۰٬۰۰۰ تومان؛ یک موقعیت هم‌زمان؛ تمام موجودی باقیمانده پس از رزرو کارمزد و گردکردن مقدار دارایی به دقت بازار. اهرم، شورت و آربیتراژ وجود ندارند.',
           '- نرخ فعلی مشاهده‌شدهٔ حساب: سفارش‌بردار ۰٫۳۰٪ هر سمت؛ سفارش‌گذار ۰٫۲۵٪. تست بر مبنای سفارش‌بردار است. کارمزد طرح اعتبار ۰ تومان مشاهده شده است. نرخ امروز به تاریخچه اعمال شده؛ نرخ تاریخی حساب ادعا نمی‌شود.',
           '- فرض پایهٔ اجرای تاریخی: فاصله کامل خریدوفروش ۰٫۲۰٪، یعنی ۰٫۱۰٪ به هر سمت قیمت کندل. هزینه رفت‌وبرگشت تقریباً ۰٫۸٪ است. دفتر تاریخی، لغزش واقعی و تأخیر واقعی در دسترس نیستند؛ این فرض جای اندازه‌گیری را نمی‌گیرد.',
           '- افت روزانه ۲٪، خرید تازه را تا روز بعدِ تهران متوقف می‌کند؛ افت ۵٪ نسبت به سرمایه اولیه، در بازشدن ساعت بعد خروج و توقف حساب را فعال می‌کند. این بررسی ساعتی ممکن است از آستانه عبور کند.',
           '- رسیدن به هدف سود بر اساس ارزش قابل نقدکردن حساب پس از کارمزد فروش محاسبه می‌شود. پس از تحقق، خرید تازه تا پایان همین دوره ممنوع است. این قاعده پس از دور اول ناموفق به طرح پژوهش اضافه شد و تغییر پنهان نیست.', '',
           '## میزان انطباق با پرامپت انتخاب نماد','',
           '**بک‌تست کامل تاریخیِ فرمول ۴۰/۳۰/۲۰/۱۰ ممکن نبود:** دفتر سفارش تاریخی و گردش دقیق تومانی روزهای گذشته وجود ندارد. عددی برای این دو داده اختراع نشده است.', '',
           '- در تمام روزها ابتدا فهرست ممنوع محلی با ممنوع‌های API طرح ۱۸۳ ادغام و حذف شد. ۶۲ بازار اسپات تومانیِ فعلی باقی ماندند؛ این فهرست فعلی به گذشته اعمال شده و تاریخچه کامل تغییر وضعیت بازارها در دسترس نیست.',
           '- دامنهٔ روزانه = ۱۰۰×(سقف/کف−۱). هفت روز کامل قبلی، میانگین۷، میانگین۳ و میانه۷ محاسبه شد. هفت روز معتبر، حداقل ۱۸ ساعت دارای معامله در روز قبل و حداقل ۵۰میلیون تومان **کران پایین قطعی گردش** شرط کیفیت این آزمایش‌اند.',
           '- گردش دقیق گذشته گزارش نمی‌شود. حاصل «کمترین قیمت روز × حجم دارایی روز» کران پایین و «بیشترین قیمت × حجم» کران بالاست. برای مؤلفه حجم تاریخی، رتبهٔ کران پایین استفاده شد؛ این با حجم دقیق پرامپت یکسان نیست.',
           '- امتیاز جایگزین تاریخی = (۴۰×رتبه‌درصدی دامنه۷ +۳۰×رتبه دامنه۳ +۱۰×رتبه کران پایین گردش)÷۸۰. پنج نامزد اول هر روز وارد بررسی سیگنال شدند. مؤلفه ۲۰٪ هزینه تاریخی غایب است؛ بازه ممکن امتیاز کامل نیز در فایل روزانه ثبت شده است.',
           '- ردیف دارای ساعت مفقود فقط در صورت تطبیق مجموع حجم ساعتی با حجم روزانه، بی‌معامله محسوب شد؛ قیمت نگهداری‌شده با حجم صفر سیگنال یا اجرای معامله نمی‌سازد. شکاف ناشناخته برای ساخت سیگنال مجاز نیست.',
           '- **رتبه‌بندی کامل فعلی با هر چهار وزن** در بخش جداگانه، با سه دفتر واقعی و حجم تومانی ۲۴ساعتهٔ ارائه‌شده توسط والکس محاسبه شده است. دفتر امروز به معاملات گذشته تعمیم داده نشده است.', '',
           '## همهٔ خانواده‌ها؛ نسخه انتخاب‌شده با ماه قبل','',
           'اعداد جدول از بازبینی یک‌دقیقه‌ای نسخه‌ها هستند؛ بیشترین افت حساب همچنان با ارزش‌گذاری انتهای ساعت محاسبه می‌شود. برای AI تمام مدل‌ها در اعتبارسنجی ماه قبل رد شدند، بنابراین خروجی HOLD است.', '',
           '| خانواده | تنظیم یکتا | نسخه منتخب | بازده خالص ماه هدف | سود/زیان تومان | افت ساعتی | معاملات | میانه نگهداری ساعت |',
           '|---|---:|---|---:|---:|---:|---:|---:|']
    table=[]
    for family in ('relative_strength','range_reversion','compression_breakout','exit_comparison','ai_filter','volatility_control'):
        row=summary['selected_on_previous_month'][family];key=row['id']+'-OLHC';x=micro['runs'].get(key,row)
        count=sum(r['family']==family for r in rows)
        table.append({'family':family,'id':row['id'],'config':row['config'],**{k:x[k]for k in ['return_pct','pnl_tmn','max_drawdown_pct','trades','median_hold_hours','fees_tmn']}})
        lines.append(f"| {names[family]} | {count} | `{row['id']}` | {x['return_pct']:+.3f}٪ | {x['pnl_tmn']:+,.0f} | {x['max_drawdown_pct']:.3f}٪ | {x['trades']} | {x['median_hold_hours']:.2f} |")
    dump(OUT/'report-table.json',table)
    lines+=['','انتخاب با ماه قبل، این ماه را آزمون کاملاً دست‌نخورده نمی‌کند: داده‌های آن در گفت‌وگوهای قبلی دیده شده و قاعده هدف سود نیز پس از دور اول افزوده شده است. اختلاف‌های بسیار کوچک ممیز شناور ممکن است ترتیب تنظیم‌های هم‌ارز را در فایل جست‌وجو تغییر دهند؛ نتیجه به‌صورت سود تضمین‌شده تفسیر نمی‌شود.','',
            '## تعریف و دامنه پارامترهای آزموده‌شده','',
            '| خانواده | ورود و انتخاب | خروج و پارامترها |',
            '|---|---|---|',
            '| قدرت نسبی | مقایسه بازده۲۴،۷۲ یا ترکیب دو افق پس از مقیاس‌کردن با نوسان؛ آلت‌کوین در قیاس با BTC و BTC در قیاس با تتر؛ بازده۲۴ و۷۲ بیشتر از تتر و بازده محلی۲۴ مثبت. سه حالت فیلتر بازار: ندارد، BTC مثبت و بالای EMA72، یا بیش از۵۵٪ بازار بالاتر از تتر | حداکثر۱۲/۲۴/۴۸ساعت؛ حد۵٪ یا۲ATR؛ حد سود قیمت۵٪ در یک پروفایل؛ هدف حساب خاموش/۳٪/۵٪ |',
            '| بازگشت به میانگین | نسبت تغییر خالص قیمت به مجموع تغییرهای مطلق۲۴ساعت کمتر از۰٫۲۰/۰٫۳۵؛ فاصله زیر VWAP24 حداقل۱/۱٫۵/۲ATR؛ بسته‌شدن آخرین ساعت بالاتر از ساعت قبل | هدف قیمت VWAP زمان ورود؛ حد۱٫۵/۲٫۵ATR؛ حداکثر۶/۱۲/۲۴ساعت؛ هدف حساب خاموش/۳٪/۵٪ |',
            '| فشردگی و شکست | شکست سقف۶/۱۲/۲۴ساعت قبل؛ نسبت نوسان قبل از شکست به نوسان مرجع کمتر از۰٫۵۰/۰٫۷۵؛ حجم کندل حداقل۱/۱٫۵برابر میانگین۲۴کندل قبل | پروفایل حد۳٪/هدف۵٪؛ یا۲ATR بدون هدف؛ یا۳ATR/هدف۱۰٪؛ نگهداری۱۲/۲۴/۴۸ساعت؛ هدف حساب سه حالت |',
            '| مقایسه خروج | نامزد اول با جهت۳روزه مثبت، در برنامه ثابت هر۴۸ساعت؛ ورود مستقل از نتیجه خروج قبلی زمان‌بندی شده | حد۵٪،۲ATR،۳ATR، یا تعقیب۳ATR؛ هدف قیمت خاموش/۵٪/۱۰٪؛ سقف۲۴/۴۸ساعت؛ هدف حساب سه حالت |',
            '| AI | رگرسیون Ridge تجمیعی با۱۵ویژگی علّی؛ برچسب بازده خالصِ ورود در ساعت بعد تا۶/۱۲/۲۴ساعت؛ جریمه۱۰/۱۰۰. آموزش قبل از هفت روز آخر ماه قبل؛ اعتبارسنجی در آن هفت روز با حذف برچسب‌های عبوری | شرط بهبود خطای مربعی در قیاس با پیش‌بینی بازده خالص صفر؛ آستانه سود مورد انتظار۰/۰٫۳/۱٪؛ فیلتر BTC روشن/خاموش؛ حد۲ATR؛ هدف حساب سه حالت. هر۶مدل رد شدند |',
            '| کنترل نوسان | انتخاب نامزد اول؛ جهت۳روزه مثبت روشن/خاموش | حد۵٪؛ سقف۲۴/۴۸ساعت؛ هدف حساب سه حالت |','',
            'در خانواده مقایسه خروج، برنامه فرصت‌های ورود ثابت است؛ توقف حساب می‌تواند اجرای فرصت بعدی را مسدود کند. بنابراین جدول بازده کل، اثر کل سیاست را نشان می‌دهد و به‌تنهایی یک مقایسهٔ کاملاً جفت‌شدهٔ همه معاملات نیست.','',
            '## فرمول دقیق نسخهٔ مثبت منتخب','',
            '۱. هر روز با اطلاعات هفت روز کامل قبل، پنج نامزد امتیاز جایگزین تاریخی را بساز.','۲. هر ساعت فقط کندل کامل قبلی را ببین. بازده محلی۲۴ساعته باید مثبت و بازده۲۴ و۷۲ساعته باید بیشتر از بازده هم‌زمان USDTTMN باشند.','۳. برای آلت‌کوین‌ها امتیاز قدرت = (بازده۲۴ساعته نماد−بازده۲۴ساعته BTC)÷بیشینه(۰٫۰۰۱، انحراف معیار بازده لگاریتمی۲۴ساعته×√۲۴). برای BTC، مرجع تتر است. بالاترین امتیاز مثبت را انتخاب کن.','۴. در بازشدن ساعت بعد، با فرض امکان اجرا در نخستین معاملهٔ ثبت‌شدهٔ دقیقه‌ای وارد شو. کل موجودی پس از رزرو کارمزد و گردکردن مقدار تخصیص می‌یابد. فیلتر جهت کلی بازار در این نسخه خاموش است.','۵. حد اولیه قیمت۵٪ زیر قیمت پرشدن؛ سقف نگهداری۴۸ساعت؛ هدف حساب۳٪ یا۵٪ پس از احتساب هزینهٔ فروش. پس از هدف، تا پایان دوره معامله تازه انجام نمی‌شود.','',
            '| سیاست حساب | بازده دقیقه‌ای | سود خالص تومان | موجودی نهایی | کارمزد تومان | تعداد معامله | ورود تهران | خروج تهران |',
            '|---|---:|---:|---:|---:|---:|---|---|']
    for key,label in [('relative_strength-007-goal3','هدف۳٪'),('relative_strength-007-goal5','هدف۵٪')]:
        x=micro['runs'][key+'-OLHC'];t=x['trade_log'][0]
        lines.append(f"| {label} | {x['return_pct']:+.3f}٪ | {x['pnl_tmn']:+,.0f} | {x['final_tmn']:,.0f} | {x['fees_tmn']:,.0f} | {x['trades']} | {local(t['entry_ts'])} | {local(t['exit_ts'])} |")
    lines+=['',f"دارایی هر دو اجرا BCH/TMN است. نخستین معاملهٔ دقیقه‌ای در ساعت ورود، سه دقیقه پس از شروع ساعت ثبت شده است. مقدار خرید در نسخه۵٪ **{trade['qty']} BCH**، قیمت خرید با فرض هزینه **{trade['entry_fill']:,.3f} تومان** و فروش **{trade['exit_fill']:,.3f} تومان** است. رسیدن نهایی به کمی بیش از هدف، از شکاف قیمتِ اولین معاملهٔ دقیقه فروش ناشی می‌شود؛ قیمت هدف، اجرای تضمین‌شده نیست.",'',
            '## یافته‌های مثبت پس از جست‌وجو، جدا از انتخاب ماه قبل','',
            f"از {summary['scenarios']} تنظیم، {summary['positive_both_paths']} تنظیم در هر دو ترتیب فرضی قیمت ساعتی مثبت و {summary['at_least_3pct_both_paths']} تنظیم حداقل۳٪ بوده‌اند. این‌ها آزمون‌های مستقل نیستند و بسیاری معاملات یکسان دارند. در دور اول۲۸۹ترکیب بدون توقف سود، هیچ نسخه مثبت نبود؛ افزودن قاعدهٔ توقف پس از هدف، نتیجه را تغییر داد.",'',
            'نسخهٔ بازگشت به میانگین `range_reversion-033-goal5` در ماه هدف با یک معامله FORM، **+۵٫۶۸۶٪** داشت؛ اما نتیجه ماه قبل همان نسخه حدود **−۵٫۹۸٪** است. این یک کشف پس از مشاهده ماه هدف است و جای نسخه منتخب قبلی آن خانواده را نمی‌گیرد.','',
            '## حساسیت هزینهٔ نسخهٔ قدرت نسبی','',
            '| فرض هزینه | هدف۳٪: بازده | هدف۵٪: بازده |', '|---|---:|---:|']
    for c,label in [('base','۰٫۳۰٪ هر سمت + فاصله کامل۰٫۲۰٪'),('no_spread_fee_only','فقط کارمزد۰٫۳۰٪ هر سمت'),('stress_25bps_extra_each_side','همان کارمزد +۰٫۲۵٪ هزینه اضافی هر سمت نسبت به پایه'),('zero_cost_diagnostic','بدون کارمزد و فاصله؛ فقط تشخیصی')]:
        a=micro['sensitivity']['relative_strength-007-goal3'][c];b=micro['sensitivity']['relative_strength-007-goal5'][c]
        lines.append(f"| {label} | {a['return_pct']:+.3f}٪ | {b['return_pct']:+.3f}٪ |")
    lines+=['','هر سطر شبیه‌سازی مستقلی است؛ چون قیمت لازم برای رسیدن به سود خالص با هزینه تغییر می‌کند، زمان خروج هم می‌تواند عوض شود. تفریق ستون‌ها، کارمزد خالص یک مسیر ثابت را نشان نمی‌دهد. هزینه واقعی تاریخی همچنان نامعلوم است.','',
            '## خرید و نگهداری و کنترل تصادفی','',
            '| مرجع خرید و نگهداری واقعی تا پایان همان۳۰روز | بازده خالص | افت ساعتی |', '|---|---:|---:|']
    for s,x in control['benchmarks'].items():lines.append(f"| {s} | {x['return_pct']:+.3f}٪ | {x['max_drawdown_pct']:.3f}٪ |")
    lines+=['','خرید و نگهداری بدون استاپ یا توقف سود، با همان کارمزد و فاصله فرضی محاسبه شده است. نسخهٔ دارای هدف، در این ماه از BTC و PUMP بازده کمتری دارد و زمان بسیار کمتری در بازار بوده است.','',
            '| کنترل تاریخی | هدف۳٪ | هدف۵٪ |', '|---|---:|---:|']
    rd=control['randomized_same_candidates'];rw=control['rolling_30_day_windows']
    lines.append(f"| رسیدن به هدف در۳۰۰انتخاب تصادفی میان همان نامزدهای واجد شروط قدرت نسبی | {rd['0.03']['goal_hit_count']}/۳۰۰ | {rd['0.05']['goal_hit_count']}/۳۰۰ |")
    lines.append(f"| رسیدن به هدف در۳۱پنجرهٔ۳۰روزهٔ هم‌پوشان با جابه‌جایی روز شروع | {rw['0.03']['goal_hit_count']}/۳۱ | {rw['0.05']['goal_hit_count']}/۳۱ |")
    lines.append(f"| بدترین بازده پنجره‌های۳۰روزه | {rw['0.03']['worst_return_pct']:.3f}٪ | {rw['0.05']['worst_return_pct']:.3f}٪ |")
    lines+=['','این کنترل‌ها روی همان دادهٔ بررسی‌شده‌اند. پنجره‌ها هم‌پوشان و نتایج تصادفی تکرار یک مسیر تاریخی‌اند؛ تعداد موفقیت، احتمال سود آینده یا آزمون آماری استقلال نیست. کنترل تصادفی صرفاً حساسیت انتخاب نماد درون فهرست واجد شرایط را می‌سنجد. سود یک معامله برای اثبات برتری رتبه‌بندی کافی نیست.','',
            '## رتبه‌بندی کامل فعلی با دفتر واقعی','',
            f"بیشترین میانگین دامنهٔ۷روز در بازارهای مجاز: **{current['most_volatile_allowed']['symbol']}، {current['most_volatile_allowed']['mean7']:.3f}٪**؛ میانگین۳روز {current['most_volatile_allowed']['mean3']:.3f}٪.",'',
            'سه برداشت دفتر با فاصله زمانی انجام شد. داده نوسان مربوط به هفت روز کامل منتهی به ۲۳اکتبر۰۰:۰۰UTC است. حجم۲۴ساعته از پاسخ بازار والکس در زمان دریافت آمده است. این جدول فهرست بررسی برای اکنون است و سیگنال خرید نیست.','',
            '| رتبه | نماد | میانگین۷روز | میانگین۳روز | میانه۷روز | هزینه خریدوفروش فوری با کارمزد | دامنه هزینه سه برداشت | حجم۲۴ساعت میلیاردتومان | امتیاز |',
            '|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for row in current['screens']['5000000'][:5]:
        lines.append(f"| {row['rank']} | {row['symbol']} | {row['mean7']:.3f}٪ | {row['mean3']:.3f}٪ | {row['median7']:.3f}٪ | {row['roundtrip_cost_pct']:.3f}٪ | {row['cost_min_pct']:.3f}–{row['cost_max_pct']:.3f}٪ | {row['quote_volume_24h_tmn']/1e9:.3f} | {row['score']:.2f} |")
    times=[datetime.fromisoformat(t).astimezone(TZ)for row in current['screens']['5000000']for t in row['snapshot_times_utc']]
    lines+=['',f"زمان دفترها به تهران: **{min(times).strftime('%Y-%m-%d %H:%M:%S')} تا {max(times).strftime('%H:%M:%S')}**. کارمزد۰٫۳۰٪ هر سمت در هزینه جدول منظور شده است؛ اسپرد دوباره جمع نشده است. هر ردیف هفت روز معتبر دارد. هزینه فروش فوری، پیش‌بینی هزینه خروج یک یا دو روز بعد نیست.",'',
            '| اندازه حساب/معامله | پنج نامزد اول | بازار دارای عمق کافی در هر سه برداشت |', '|---|---|---:|']
    for amount,rr in current['screens'].items():lines.append(f"| {int(amount):,} تومان | {', '.join(z['symbol'] for z in rr[:5])} | {len(rr)} |")
    flagged=[r for r in current['screens']['5000000'] if r['costly_relative_to_range'] or r['unstable_three_snapshots']]
    lines+=['','بازارهایی با هزینه بیش از یک‌چهارم دامنه معمول سه‌روزه یا تغییر هزینه بیش از۰٫۵واحددرصد در سه برداشت، با آستانه پژوهشی علامت خورده‌اند: '+(', '.join(r['symbol'] for r in flagged) if flagged else 'هیچ‌کدام در این برداشت‌ها')+'. آستانه‌ها اثبات کیفیت معامله نیستند. جزئیات همه بازارها و هر اندازه، در CSV و JSON همراه آمده است.','',
            '## کنترل داده و محدودیت‌ها','',
            '- نسخه‌های مثبت اصلی و همه نسخه‌های منتخب هر خانواده، دقیقه‌های لازم را با حجم ساعتی تطبیق دادند؛ هر دو ترتیب OHLC/OLHC داخل دقیقه برای آن‌ها نتیجه یکسان داد. وجود قیمت معامله، وجود عمق کافی برای سفارش ما را ثابت نمی‌کند.',
            '- در دو ساعت از دو اجرای تشخیصی دیگر، حجم دقیقه و ساعت سازگار نیست: ARB و S در۲۶سپتامبر۱۰:۰۰UTC. این اختلاف در فایل ثبت شده است. نسخه‌های اصلی هدف۳٪/۵٪ و جدول منتخب خانواده‌ها به این دو ساعت متکی نیستند.',
            '- تعداد مدل‌ها/سناریوها و شکست‌ها ثبت شده است. انتخاب نسخه با ماه قبل هم خطر بیش‌برازش کل پژوهش را رفع نمی‌کند؛ همان داده‌ها در گفت‌وگوهای دیگر بررسی شده‌اند.',
            '- اجرای قیمت کندلی، کارمزد تاریخی، صف سفارش، پرشدن ناقص، قطع API، انتشار دیرهنگام داده و دفتر تاریخی، بازسازی نشده‌اند. تاریخچه بازارهای حذف‌شده نیز کامل نیست.',
            '- ادعای سود واقعی یا آمادگی معامله زنده وجود ندارد. مرحله لازم برای اعتبارسنجی، ثابت‌کردن نسخه و آزمون سایه روی دادهٔ پس از انتخاب است.','',
            '## فایل‌ها و بازاجرا','',
            '- `all-scenarios.csv` و `all-scenarios.json`: همه۸۶۷تنظیم، پارامترها، نتیجه دو مسیر، ماه قبل، سود/زیان، کارمزد، افت و تعداد معاملات.',
            '- `minute-replay.json`: نسخه‌های منتخب، زمان/قیمت/مقدار/دلیل هر معامله، پوشش دقیقه، حساسیت هزینه و اختلاف‌های داده.',
            '- `current-full-screen.csv`: چهار اندازه، رتبه‌ها، نوسان، VWAP خریدوفروش، کارمزد، هزینه و پایداری سه برداشت.',
            '- `daily-screens.json`: فهرست روزانه با اطلاعات پیش از تصمیم، امتیاز جایگزین و حدود ممکن امتیاز کامل.',
            '- `ai-models.json`، `data-audit.json` و `controls.json`: اعتبارسنجی مدل، ممیزی تاریخچه و مقایسه‌ها.',
            '- `stage1/`: نتایج دور اول ناموفق؛ تغییر قاعده هدف سود پنهان نشده است.','',
            '```sh', 'PYTHONPATH=src:experiments OPENBLAS_NUM_THREADS=1 python3 experiments/research30.py',
            'PYTHONPATH=src:experiments python3 experiments/research30_verify.py',
            'PYTHONPATH=src:experiments python3 experiments/research30_controls.py',
            'PYTHONPATH=src:experiments python3 experiments/research30_report.py',
            'PYTHONPATH=src:experiments python3 -m unittest discover -s experiments -p test_research30.py -v', '```','',
            'بازاجرا به NumPy و داده‌های منجمد همراه نیاز دارد. دریافت داده از `https://api.wallex.ir/v1/udf/history`، دفتر `https://api.wallex.ir/v1/depth`، فهرست `https://api.wallex.ir/hector/web/v1/markets` و ممنوع‌های `https://api.wallex.ir/v1/prop/packages/183` انجام شده است. در فایل هر برداشت، درخواست و زمان آن ثبت شده؛ هیچ کلید API لازم نبود.']
    Path('docs/RESEARCH30_ALL_METHODS_FA.md').write_text('\n'.join(lines)+'\n')
    print('REPORT_READY docs/RESEARCH30_ALL_METHODS_FA.md',flush=True)
    print('CURRENT_TOP5',[(r['symbol'],round(r['roundtrip_cost_pct'],3),round(r['score'],2))for r in current['screens']['5000000'][:5]],flush=True)


if __name__=='__main__':main()
