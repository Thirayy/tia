import httpx
from sqlmodel import Session, select, text
from app.database import engine
from app.models import QuranPage

def fix_and_reseed_quran():
    print("🧹 Membersihkan tabel quran_pages yang salah hitung...")
    with Session(engine) as session:
        # Kosongkan tabel dan reset ID
        session.exec(text("TRUNCATE TABLE quran_pages RESTART IDENTITY;"))
        session.commit()
        print("✅ Tabel quran_pages berhasil dikosongkan.")

    print("🚀 Memulai re-seeding data Quran (604 Halaman) dengan logika Kaca per Juz yang BENAR...")
    
    current_juz = None
    kaca_counter = 1

    with Session(engine) as session:
        for page_num in range(1, 605):
            try:
                resp = httpx.get(f"https://api.alquran.cloud/v1/page/{page_num}/quran-uthmani", timeout=15.0)
                if resp.status_code == 200:
                    data = resp.json().get("data", {})
                    ayahs = data.get("ayahs", [])
                    if not ayahs:
                        continue
                    
                    first_ayah = ayahs[0]
                    last_ayah = ayahs[-1]
                    
                    juz = first_ayah.get("juz")
                    surah_id = first_ayah.get("surah", {}).get("number")
                    surah_name = first_ayah.get("surah", {}).get("englishName")
                    
                    # 💡 LOGIKA PERBAIKAN: Reset kaca = 1 HANYA jika Juz berganti
                    if juz != current_juz:
                        current_juz = juz
                        kaca_counter = 1
                    else:
                        kaca_counter += 1

                    ayat_start = first_ayah.get("numberInSurah")
                    ayat_end = last_ayah.get("numberInSurah")
                    
                    qpage = QuranPage(
                        page_number=page_num,
                        juz=juz,
                        kaca=kaca_counter,  # <--- Kaca berurutan murni per Juz!
                        surah_id=surah_id,
                        surah_name=surah_name,
                        ayat_start=ayat_start,
                        ayat_end=ayat_end
                    )
                    session.add(qpage)
                    print(f" Page {page_num} -> Juz {juz} Kaca {kaca_counter} ({surah_name} {ayat_start}-{ayat_end})")
            except Exception as e:
                print(f"❌ Error page {page_num}: {e}")
        
        session.commit()
        print("🎉 Re-seeding Selesai & Data Sudah Presisi!")

if __name__ == "__main__":
    fix_and_reseed_quran()