# Blind trial

A test of the tasks our answer key does not cover, done by people, with the key made before Kaava answers.

**Status: not yet run.** The rig below is built and tested. No trial has been done by people yet, so this page reports no result. The [Results](#results) section stays empty until one has.

## Why

Kaava scores 27 of 27 on Stoa's four sample sheets against a key we wrote ourselves ([EVALUATION.md](EVALUATION.md)). That key holds questions whose answer a person can read off the sheet in a minute: a figure in a table, a sum of printed figures, a handful of labels.

It leaves out the three answers the demo shows off:

| Shown in the demo | Why it is not in the key |
|---|---|
| The count of every door on the school plan (118) | Nobody on the team counted the doors by hand first. |
| The area of the Multi-Purpose Hall, traced on the sheet | Nobody measured the hall by hand, so there is no independent figure to hold it against. |
| The length of baseboard in that hall | Same. |

So for those three we know what Kaava says and what it shows as evidence. We do not know, from an independent source, whether it is right. A blind trial is how we find out for one question on one sheet.

The door count needs it most. Kaava marks that answer Check this, and its own runs do not agree: the answer kept with the app is 118 doors, with 5 more boxed in only one of its two passes and drawn as doubtful; a fresh run of the same question on 15 November 2026 gave 122 (`results/kept_costs/`). The true count is somewhere near; only a count made by hand says where. The hall area and the baseboard length came out the same on a fresh run (245.0 m² and 50.4 m), which shows they are repeatable, not that they are right.

## Who is needed

- **One marker.** Makes the key. Must not have seen Kaava's answer to this question on this sheet. Ideally someone who did not build Kaava.
- **Three verifiers.** People who have not used Kaava. Each checks Kaava's answer from the evidence on the sheet and is timed.
- **A fresh sheet.** One that Kaava was never tuned on and has no answer on file for. Not one of the four sample sheets: their answers ship with the app. A drawing from a project of your own, or any plan the team has not opened in Kaava, will do.

The whole exercise takes about 20 minutes, most of it the marker's counting.

## Steps

1. **Bring the sheet in.** Open the app, choose "Bring your own" (under "Choose a drawing") and upload the sheet: an image, or a PDF, of which you then choose the page. Do not ask it anything. Press "Blind trial on this sheet" under the drawing, or copy the address of the page for step 2. The trial uses the sheet as the app shows it when the drawing is opened.
2. **Make the key.** The marker opens `/blind`, pastes that address, and:
   - writes their name and the question, word for word as Kaava will be asked;
   - for a count, taps each item on the sheet (tap a mark again to remove it; drag to move; pinch or scroll to zoom);
   - for a length, an area or a volume, measures it their own way (printed dimensions and a calculator) and types the number, in metres, m² or m³, with a line on how they got it;
   - ticks "I have not seen Kaava's answer to this question on this sheet" and presses **Lock the key**.

   A locked key cannot be changed. The page moves to the trial's own address, `/blind?t=<id>`.
3. **Run.** On the trial's page, press **Run Kaava and the model alone, once each**. Kaava is asked the way the app asks. The model alone is the same vision model with the same settings, given the image and the question and nothing else. Each is asked once and its answer is kept; pressing again shows the record, it never asks again.
4. **Verify.** Send the verifier link shown on the trial's page (`/blind?t=<id>&verify=1`) to three people. Each one enters a name and presses Start. Only then do they see the question, Kaava's answer and the items it counted, numbered on the sheet. They choose "Kaava's answer is right", "Kaava's answer is wrong" or "I can't tell". The time from Start to that choice is recorded. They see the key only after they have answered.
5. **Export.** From the repository:

   ```
   python scripts/blind_trial.py export <id> --from https://stoa.ahmedxsaad.me
   ```

   This writes `results/blind/<date>-<id>.json`. It refuses a trial that lacks the key, either answer, or three checks, unless `--partial` is given, in which case the file says what is missing.

## What is recorded

| | From | Notes |
|---|---|---|
| The correct quantity | The marker | With who, when, the marks, and how it was measured. |
| Kaava's answer | The run | Its state (Verified, Check this, Can't answer reliably), its evidence, and whether it refused. A refusal is recorded as a refusal, not as a wrong answer. |
| The model alone's answer | The run | Same question, same image, same model. |
| Missed marks | Key against evidence | Key marks that no item boxed by Kaava accounts for. |
| Added marks | Key against evidence | Items Kaava boxed that no key mark accounts for. |
| Cost when worked out | The run | What each answer cost in model calls. If Kaava's answer was already on file it is shown again at no further cost, and the record says so and still gives the original cost. |
| Response time | The run | The time the answer took to work out. |
| Each verifier's verdict and seconds | The verifiers | Timed in the browser and, separately, on the server's clock. Whether the verdict agrees with the key is worked out, not entered. |

Both answers are scored against the key with the dataset's rule: a count must match exactly, a length, area or volume must be within 2%.

## The rules the rig enforces

- **The key comes first.** A trial starts when a key is locked. There is no way to change a key, and no way to add one to a trial after Kaava has answered.
- **The key stays hidden until Kaava has answered.** Before the run, the trial's record gives only who made the key and when.
- **Each system answers once.** If the call to the model alone fails (a network error is not an answer), that call can be tried again; Kaava is not asked again.
- **Marks are paired one to one.** A mark belongs to an item when it lies on it, or beside it by less than half the item's own size and never more than 1.5% of the sheet's long edge. Each item takes one mark and each mark one item, pairing as many as can be paired.
- **Marks are compared only when that is meaningful.** If the marker placed fewer marks than the count they entered, or Kaava computed its answer from printed figures instead of boxing each item, or Kaava refused, the record says "not comparable" and why, in place of a number.
- **A verifier's time must be between one second and one hour.**
- **A test of the rig is not a result.** Trials made with `rig=1` are labelled and cannot be exported.

## What a trial does not show

- **It is one question on one sheet.** It tells us whether Kaava was right there. It is not an error rate. Several trials on different sheets would be needed for that.
- **The key can be wrong too.** Two people counting 100 doors may not agree. If there is time, have a second marker make a second trial on the same sheet and question, and compare the keys.
- **"I have not seen the answer" is the marker's word.** The rig records whether an answer was already on file when the key was locked; it cannot know what the marker looked at.
- **A verifier could open the full record.** The verifier page does not show the key, the model alone, or the other verifiers' verdicts, but the trial's main address does once the run is done. Send verifiers the verifier link only.
- **One sheet per trial.** The sheet marked and asked about is the one the app shows when the drawing is opened. A PDF of several pages is brought in one page at a time: choose the page in the app first (each page is a drawing with its own address), then start the trial from that address.

## Where things are

- The page: `/blind` (source: `src/blueprints/blind_page.html`).
- The API and the pairing of marks: `src/blueprints/blind.py`. Tests: `tests/test_blind.py`.
- Trials in progress: `cache/blind/<id>.json` on the server where the trial was made.
- Finished trials: `results/blind/`, written by `scripts/blind_trial.py export`. `python scripts/blind_trial.py list` shows the trials on this machine.

## Results

None yet. No blind trial has been run by people.

When one has, this section will give, for each trial: the sheet and the question, the key's quantity, Kaava's answer and the model alone's answer with whether each matches the key, the marks missed and added, what each answer cost and how long it took, and the three verifiers' verdicts with their times.
