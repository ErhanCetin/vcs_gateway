# Migrations — Kullanım Kılavuzu

## Ön Koşul

```bash
# Infra container'ları çalışıyor olmalı
docker compose -f docker-compose.infra.yml ps
# postgres → healthy

# .venv aktif olmalı
source .venv/bin/activate
```

---

## Sık Kullanılan Komutlar

### Tüm migration'ları uygula (en güncel hale getir)
```bash
DATABASE_URL=postgresql://appuser:apppassword@localhost:5432/vcs_gateway_db \
  alembic upgrade head
```

### Mevcut durumu gör (hangi revision'da olduğunu)
```bash
DATABASE_URL=postgresql://appuser:apppassword@localhost:5432/vcs_gateway_db \
  alembic current
```

### Migration geçmişini listele
```bash
DATABASE_URL=postgresql://appuser:apppassword@localhost:5432/vcs_gateway_db \
  alembic history
```

### Bir önceki revision'a geri dön
```bash
DATABASE_URL=postgresql://appuser:apppassword@localhost:5432/vcs_gateway_db \
  alembic downgrade -1
```

### Sıfıra dön (tüm tabloları sil)
```bash
DATABASE_URL=postgresql://appuser:apppassword@localhost:5432/vcs_gateway_db \
  alembic downgrade base
```

---

## Yeni Migration Oluşturma

```bash
# Boş migration dosyası oluştur (manuel SQL yazacaksan)
DATABASE_URL=postgresql://appuser:apppassword@localhost:5432/vcs_gateway_db \
  alembic revision -m "add_my_table"
# → migrations/versions/<hash>_add_my_table.py oluşur
# → upgrade() ve downgrade() fonksiyonlarını doldur
```

---

## DB Doğrulama (psql)

```bash
# vcs_gateway_schema tabloları
docker exec vcs-gateway-postgres-1 psql -U appuser -d vcs_gateway_db \
  -c "\dt vcs_gateway_schema.*"

# shared_schema tabloları
docker exec vcs-gateway-postgres-1 psql -U appuser -d vcs_gateway_db \
  -c "\dt shared_schema.*"

# Test tenant kontrolü
docker exec vcs-gateway-postgres-1 psql -U appuser -d vcs_gateway_db \
  -c "SELECT tenant_id, tenant_name, slug FROM shared_schema.tenant"

# Whitelist seed kontrolü
docker exec vcs-gateway-postgres-1 psql -U appuser -d vcs_gateway_db \
  -c "SELECT vcs_provider, event_type, event_action FROM shared_schema.vcs_event_whitelist"
```

---

## Sıfırdan Başlama (volumes dahil)

```bash
# Container + volume'ları tamamen sil
docker compose -f docker-compose.infra.yml down -v

# Yeniden başlat
docker compose -f docker-compose.infra.yml up -d

# Migration uygula
DATABASE_URL=postgresql://appuser:apppassword@localhost:5432/vcs_gateway_db \
  alembic upgrade head
```

---

## Notlar

- `0001_initial.py` → `vcs_gateway_schema`: `outbox_event` + `inbound_event`
- `0002_seed_shared_schema.py` → `shared_schema`: customer, tenant, tenant_vcs_config, vcs_event_whitelist + seed data
- `shared_schema` migration'ları **production'da platform pipeline'ı** tarafından yönetilir. Tüm DDL `IF NOT EXISTS` kullandığından her ortamda güvenle çalışır.
