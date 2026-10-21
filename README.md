# MyENGIE (personal) – Home Assistant

Integrare proprie pentru contul **my.engie.ro** (gaz + energie electrică). Fără licență, fără server extern: vorbește direct cu API-ul aplicației MyENGIE.

Domeniu: `myengie_personal` — poate rula în paralel cu integrarea `myengie` a lui cnecrea (entitățile nu se suprapun).

## Instalare

1. Copiază `custom_components/myengie_personal` în `/config/custom_components/`
   (sau adaugă repo-ul ca *Custom repository* în HACS, categoria *Integration*).
2. Repornește Home Assistant.
3. **Setări → Dispozitive și servicii → Adaugă integrare → MyENGIE (personal)**.
4. Introdu emailul și parola din aplicația MyENGIE. Intervalul implicit e 1 oră (minim 15 minute, modificabil din *Opțiuni*).

## Ce primești

Un dispozitiv per loc de consum per utilitate (ex. *MyENGIE 5001… Gaz*), plus un dispozitiv *MyENGIE cont*.

**Per utilitate**

| Entitate | Ce arată |
|---|---|
| `…_sold_total` | Sold total (RON) |
| `…_factura_restanta` | Da/Nu + fiecare factură neachitată cu zilele până la / peste scadență |
| `…_rest_de_plata` | Suma neachitată (RON) |
| `…_ultima_factura` | Valoarea ultimei facturi + număr, emitere, scadență, achitată |
| `…_arhiva_facturi` | Nr. facturi în anul curent + lista lor |
| `…_arhiva_plati` | Nr. plăți în anul curent + lista lor |
| `…_citire_permisa` | Da/Nu dacă azi e în perioada de autocitire + interval, zile rămase |
| `…_consum_anual` | Consum facturat în anul curent (kWh) + per lună |
| `…_revizie_tehnica` | Validă/Expirată + date revizie/verificare (doar gaz) |
| `…_date_contract` | Status contract + detalii (diagnostic) |
| `…_date_utilizator` | Titularul contractului + contact, adrese (diagnostic) |
| `…_factura_electronica` | Activă/Inactivă (diagnostic) |
| `…_distribuitor` | Operatorul de distribuție (diagnostic) |
| `button.…_actualizeaza` | Reîncarcă datele imediat |

**Per contor (instalație)**

| Entitate | Ce arată |
|---|---|
| `sensor.…_index_contor_<inst>` | Ultimul index (m³ / kWh) – compatibil Energy Dashboard |
| `sensor.…_istoric_citiri_<inst>` | Nr. citiri din ultimii 3 ani + fiecare citire cu tipul ei |
| `sensor.…_conventie_consum_<inst>` | Convenția de consum pe luna curentă + toate lunile |
| `number.…_index_nou_<inst>` | Câmpul în care scrii indexul de trimis |
| `button.…_trimite_index_<inst>` | Trimite autocitirea |

**Cont**: `sensor.myengie_personal_notificari_necitite` + ultimele 10 notificări.

## Trimiterea autocitirii

Nu mai trebuie creat manual `input_number` în `configuration.yaml`:

1. Scrie valoarea în `number.myengie_personal_…_index_nou_<inst>`.
2. Apasă `button.myengie_personal_…_trimite_index_<inst>`.

Butonul refuză un index mai mic decât ultimul și afișează eroarea ENGIE dacă trimiterea e respinsă.

Sau din automatizări / scripturi:

```yaml
action: myengie_personal.trimite_index
data:
  installation: "4001573662"   # din atributele senzorului Index contor
  index: 8250
```

Card rapid pentru dashboard:

```yaml
type: entities
title: Autocitire gaz
entities:
  - sensor.myengie_personal_<poc>_gaz_citire_permisa
  - sensor.myengie_personal_<poc>_gaz_index_contor_<inst>
  - number.myengie_personal_<poc>_gaz_index_nou_<inst>
  - button.myengie_personal_<poc>_gaz_trimite_index_<inst>
```

## Detalii tehnice

- Login OAuth (token 2h) + refresh automat; tokenul se păstrează la repornire, deci nu se face login la fiecare restart.
- Parolă schimbată → Home Assistant cere reautentificare (nu trebuie ștearsă integrarea).
- Plățile, factura electronică, distribuitorul, serviciile tehnice și notificările se citesc la fiecare al 6-lea ciclu (~6h); restul la fiecare ciclu.
- Serverul ENGIE intră uneori în mentenanță (răspunde cu HTML) — entitățile devin temporar indisponibile și revin singure.
- *Descarcă diagnosticul* din pagina integrării dă datele cu informațiile personale anonimizate.
