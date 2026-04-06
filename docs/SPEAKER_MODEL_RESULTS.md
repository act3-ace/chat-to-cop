# Speaker Model Evaluation Report (RQ1)

**Labels**: data\labels\dash3_silver_labels_opus.jsonl

## Summary

| Metric | With Speakers | Without Speakers |
| --- | --- | --- |
| Labels | 935 | 935 |
| Matched extractions | 337 | 358 |
| Match rate | 36.0% | 38.3% |
| Type exact match | 19.6% | 20.2% |
| Type relaxed match | 19.8% | 20.4% |
| Entity overlap (avg Jaccard) | 27.0% | 29.8% |
| Noise detection accuracy | 100.0% | 100.0% |
| Confidence correlation | 0.071 | 0.033 |

## Delta (with - without speaker models)

- Type exact match: **-0.6%**
- Type relaxed match: **-0.6%**
- Entity overlap: **-2.8%**
- Noise accuracy: **+0.0%**

## Per-Type Breakdown: With Speakers

| Type                      |  Correct |    Total |     Rate |
| ------------------------- | -------- | -------- | -------- |
| csar                      |        1 |        1 |    100% |
| cyber_ew                  |        9 |       17 |     53% |
| entity_id                 |        2 |       12 |     17% |
| environmental             |        0 |        1 |      0% |
| fire_mission              |        3 |        3 |    100% |
| fuel                      |        8 |       13 |     62% |
| handover                  |        0 |        1 |      0% |
| location                  |        7 |        8 |     88% |
| none                      |       60 |      645 |      9% |
| sitrep                    |        4 |        9 |     44% |
| status_change             |       15 |       34 |     44% |
| tasking                   |       54 |      125 |     43% |
| threat                    |       21 |       63 |     33% |
| weapons                   |        1 |        3 |     33% |

## Per-Type Breakdown: Without Speakers

| Type                      |  Correct |    Total |     Rate |
| ------------------------- | -------- | -------- | -------- |
| csar                      |        1 |        1 |    100% |
| cyber_ew                  |        8 |       17 |     47% |
| entity_id                 |        1 |       12 |      8% |
| environmental             |        0 |        1 |      0% |
| fire_mission              |        3 |        3 |    100% |
| fuel                      |        9 |       13 |     69% |
| handover                  |        0 |        1 |      0% |
| location                  |        7 |        8 |     88% |
| none                      |       60 |      645 |      9% |
| sitrep                    |        5 |        9 |     56% |
| status_change             |       14 |       34 |     41% |
| tasking                   |       59 |      125 |     47% |
| threat                    |       23 |       63 |     37% |
| weapons                   |        1 |        3 |     33% |

## Learning Curves: With Speakers

Rolling accuracy by message count for speakers with 5+ messages:

- **afrl_lavgn** (601 msgs): msg5=40%, msg10=40%, msg20=25%, msg601=12%
- **VEGAS_SL** (92 msgs): msg5=80%, msg10=50%, msg20=35%, msg92=29%
- **VEGAS_PIT_A** (50 msgs): msg5=20%, msg10=10%, msg20=10%, msg50=12%
- **Vegas_ABM1** (31 msgs): msg5=60%, msg10=60%, msg20=40%, msg31=32%
- **VEGAS_ABM4** (24 msgs): msg5=60%, msg10=40%, msg20=35%, msg24=33%
- **VEGAS_ABM2** (19 msgs): msg5=40%, msg10=20%, msg19=32%
- **vegas_pit_b** (19 msgs): msg5=20%, msg10=20%, msg19=26%
- **vegas_abm3** (17 msgs): msg5=60%, msg10=40%, msg17=41%
- **CRUSHER_SL** (14 msgs): msg5=60%, msg10=60%, msg14=57%
- **FLOATER_02_MAYA** (9 msgs): msg5=60%, msg9=67%

## Learning Curves: Without Speakers

Rolling accuracy by message count for speakers with 5+ messages:

- **afrl_lavgn** (601 msgs): msg5=40%, msg10=40%, msg20=25%, msg601=12%
- **VEGAS_SL** (92 msgs): msg5=80%, msg10=50%, msg20=35%, msg92=32%
- **VEGAS_PIT_A** (50 msgs): msg5=20%, msg10=10%, msg20=10%, msg50=14%
- **Vegas_ABM1** (31 msgs): msg5=60%, msg10=60%, msg20=40%, msg31=32%
- **VEGAS_ABM4** (24 msgs): msg5=60%, msg10=40%, msg20=30%, msg24=29%
- **VEGAS_ABM2** (19 msgs): msg5=40%, msg10=20%, msg19=26%
- **vegas_pit_b** (19 msgs): msg5=20%, msg10=20%, msg19=26%
- **vegas_abm3** (17 msgs): msg5=60%, msg10=50%, msg17=53%
- **CRUSHER_SL** (14 msgs): msg5=60%, msg10=60%, msg14=57%
- **FLOATER_02_MAYA** (9 msgs): msg5=60%, msg9=67%