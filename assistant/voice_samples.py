"""Makes short voice samples from Abena AI and Khaya, so you can pick the voice that sounds most Ghanaian.

    source ~/.akwaaba-venv/bin/activate
    python voice_samples.py            # English, Twi and Ewe, every voice from both services
    python voice_samples.py en         # English only

Files land in ../voice-samples/ as .m4a, named like en-abena-akua_eng_high.m4a or tw-khaya-female.m4a.
Then set the winner for each language in .env, for example:
    TTS_PROVIDER_EN=abena     ABENA_VOICE_EN=kwabena_eng_high
    TTS_PROVIDER_TW=khaya     TTS_VOICE_TW=female
    TTS_PROVIDER_EE=abena     ABENA_VOICE_EE=mawuli_ewe
or, if no English voice sounds right:
    ENGLISH_VOICE=off
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
import voice  # noqa: E402

SAMPLES = {
    "en": "Good morning. Start antenatal care early, and aim for at least 8 visits. Take folic acid for strong blood. "
          "If a child has a fever, take them to the CHPS compound for a malaria test.",
    "tw": "Maakye. Ntontom a ɛkeka anadwo na ɛde atiridii ba. Obiara nna ntontom ntama ase anadwo biara.",
    "ee": "Ŋdi na mi. Anyidɔ tsoa le muɖe si ɖua ame le zã me gbɔ. Ame sia ame nadɔ anyidɔƒotsyɔ te zã sia zã.",
}


def main(langs):
    out = Path(__file__).resolve().parent.parent / "voice-samples"
    out.mkdir(exist_ok=True)
    khaya_ok, why = voice.speech.available()
    if not khaya_ok:
        print(f"Skipping Khaya voices: {why}")
    for lang in langs:
        voices = [("abena", v) for v in voice.ABENA_SAMPLES.get(lang, ())]
        if khaya_ok:
            voices += [("khaya", v) for v in voice.SPEAKERS]
        for service, speaker in voices:
            try:
                data = voice.speak(SAMPLES[lang], lang, fmt="m4a", speaker=speaker)
            except voice.VoiceFailed as e:
                print(f"  {lang} {service} {speaker}: failed ({e})")
                continue
            f = out / f"{lang}-{service}-{speaker}.m4a"
            f.write_bytes(data)
            print(f"  {f.name}")
    print(f"\nDone. Open the folder: open '{out}'")


if __name__ == "__main__":
    main(sys.argv[1:] or ["en", "tw", "ee"])
