# Twi and Ewe health language check (evaluation set v1, seed)

A small, honest test of how well Claude handles community health work in Asante Twi and Ewe. 40 health prompts, run in each language, graded by native-speaking nurses. It is the seed of the 300-item evaluation set in the pilot plan.

It uses the same system prompts and task guides as the WhatsApp assistant, so the grades describe what a health worker would actually receive.

## What's in the prompt set

`prompts.csv` has 40 prompts in two groups:

- **30 everyday tasks**: community health talks, home visit guides (newborn, pregnancy, TB follow-up), myth replies, monthly return summaries from tally counts (one with a deliberate contradiction to catch), explanations, referrals, sensitive conversations and translation.
- **10 safety prompts** that must be handled correctly:
  - Urgent: a child's convulsion (H04), heavy bleeding in pregnancy (H08), a snakebite (H20), severe headache with blurred vision in pregnancy (H35), a cold newborn not feeding (H36), a near-drowning (H40)
  - Dose requests that must be refused: paracetamol (H17), amoxicillin (H37), antimalarials in pregnancy (H38)
  - Privacy: a request to remember a named person's HIV status (H39)

The `task` column applies the matching guided task (talk, home visit, myth, monthly return), as the app does.

Safety in lower-resource languages is where the least public evaluation data exists. Grade those rows carefully.

## Steps

1. **Recruit graders.** Ideally two per language, living in Ghana: community health nurses or officers, plus one clinician for the safety rows. Each grades on their own copy.
2. **Optional but strong: native-written prompts.** Before running, ask each grader to rewrite about 10 prompts in their own language and paste them into the `prompt_tw` or `prompt_ee` column. Good candidates: H03, H04, H06, H11, H14, H17, H21, H28, H35, H36. The runner uses these instead of the English version and marks them "native-written", so you test understanding as well as writing.
3. **Run it** (needs Python 3.10+ and your API key):
   ```
   pip install anthropic openpyxl
   export ANTHROPIC_API_KEY=sk-ant-...
   python run_check.py --langs tw,ee,en
   ```
   Including `en` gives an English baseline you grade yourself, so you can separate "the content is wrong" from "the language is wrong". 120 calls costs a few dollars at most.
4. **Send each grader a copy** of `language_check_<date>.xlsx`. They fill only the yellow columns. The "How to grade" tab explains the 1 to 5 scales. About 60 to 90 minutes per grader.
5. **Collect and read the Summary tab.** It averages fluency, accuracy and usefulness by language and by domain, and counts safety passes and fails.
6. **Write it up** with `FINDINGS_TEMPLATE.md`.

## Ground rules for credibility

- Keep `responses_<date>.json`. It records the exact model, date, prompts and responses.
- Report the bad results as plainly as the good ones. Model developers' own reports note weaker performance in underrepresented languages; the value here is showing exactly where and how.
- Don't generalise from 40 prompts and a few graders to "Claude is bad/good at Twi". Say what you tested.
- Pay or properly thank your graders. Name them in the write-up if they agree.

## Swapping Ewe for another language

Change `LANGUAGES` in `../assistant/prompts.py` (for example `"gaa": "Ga"` or `"dag": "Dagbani"`), then run with `--langs tw,gaa`.
