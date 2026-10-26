from postgres_connection import get_postgres_connection

def upsert_generation(plant_id, generation_date, actual_kwh):
    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                INSERT INTO daily_generation (
                    plant_id, generation_date, actual_kwh, synced_at
                )
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (plant_id, generation_date)
                DO UPDATE SET
                    actual_kwh = EXCLUDED.actual_kwh,
                    synced_at = NOW();
            
            
            """, (plant_id, generation_date, actual_kwh))
    
def upsert_generation_batch(records):
    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany("""
                INSERT INTO daily_generation (
                    plant_id, generation_date, actual_kwh, synced_at
                )
                VALUES (%s, %s, %s, NOW())
                ON CONFLICT (plant_id, generation_date)
                DO UPDATE SET
                    actual_kwh = EXCLUDED.actual_kwh,
                    synced_at = NOW();                
            """, records)

def upsert_plants(plants):
    with get_postgres_connection() as connection:
        with connection.cursor() as cursor:
            cursor.executemany("""
                INSERT INTO plants (plant_id, sap_id, plant_name)
                VALUES (%s, %s, %s)
                ON CONFLICT (plant_id)
                DO UPDATE SET
                    sap_id = EXCLUDED.sap_id,
                    plant_name = EXCLUDED.plant_name,
                    updated_at = NOW()
            """, plants)

