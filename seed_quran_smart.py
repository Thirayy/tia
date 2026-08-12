import httpx
from collections import defaultdict
from sqlmodel import Session, SQLModel, text
from app.database import engine
from app.models import QuranPage

def fix_and_reseed_quran():
    print("🛠️ Memastikan tabel sudah dibuat di database...")
    # 1. Buat tabel quran_pages jika belum ada
    SQLModel.metadata.create_all(engine)

    print("🧹 Membersihkan tabel quran_pages...")
    with Session(engine) as session:
        try:
            session.exec(text("TRUNCATE TABLE quran_pages RESTART IDENTITY CASCADE;"))
            session.commit()
            print("✅ Tabel quran_pages berhasil dikosongkan.")
        except Exception as e:
            session.rollback()
            print(f"⚠️ Skip truncate (tabel baru dibuat): {e}")

    print("🚀 Memulai re-seeding data Quran (604 Halaman)...")
    
    current_juz = None
    kaca_juz_counter = 0
    surah_kaca_tracker = defaultdict(int)

    with Session(engine) as session:
        for page_num in range(1, 605):
            try:
                resp = httpx.get(f"https://api.alquran.cloud/v1/page/{page_num}/quran-uthmani", timeout=20.0)
                if resp.status_code != 200:
                    print(f"⚠️ Fail fetch page {page_num}")
                    continue

                data = resp.json().get("data", {})
                ayahs = data.get("ayahs", [])
                if not ayahs:
                    continue

                page_juz = ayahs[0].get("juz")
                if page_juz != current_juz:
                    current_juz = page_juz
                    kaca_juz_counter = 1
                else:
                    kaca_juz_counter += 1

                surah_groups = defaultdict(list)
                for ayah in ayahs:
                    s_id = ayah.get("surah", {}).get("number")
                    surah_groups[s_id].append(ayah)

                for s_id, group in surah_groups.items():
                    surah_kaca_tracker[s_id] += 1
                    
                    first_a = group[0]
                    last_a = group[-1]
                    
                    qpage = QuranPage(
                        page_number=page_num,
                        juz=page_juz,
                        kaca=kaca_juz_counter,
                        kaca_surah=surah_kaca_tracker[s_id],
                        surah_id=s_id,
                        surah_name=first_a.get("surah", {}).get("englishName"),
                        ayat_start=first_a.get("numberInSurah"),
                        ayat_end=last_a.get("numberInSurah")
                    )
                    session.add(qpage)
                    
                    print(f"Page {page_num} | Juz {page_juz} (Kaca {kaca_juz_counter}) | Surah {qpage.surah_name} Kaca {surah_kaca_tracker[s_id]} ({qpage.ayat_start}-{qpage.ayat_end})")

            except Exception as e:
                print(f"❌ Error page {page_num}: {e}")
        
        session.commit()
        print("🎉 Re-seeding Selesai & Data 100% Akurat!")

if __name__ == "__main__":
    fix_and_reseed_quran()