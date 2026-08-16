/**
 * form_submit_trigger.gs — pastes into the responses spreadsheet's
 * Apps Script editor. Setup instructions: AUTOMATION_SETUP.md step 6.
 *
 * Why this exists: GitHub Actions can only poll on a schedule, and
 * scheduled runs on this repo have arrived up to 2.5 hours late. Asking
 * a question on Sunday night and getting an answer on Monday morning is
 * no use once the week has started. This fires the moment any form is
 * submitted, so the pipeline runs within about a minute and the coach's
 * reply is waiting by the time the phone is back in a pocket.
 *
 * It sends a repository_dispatch event — no data, just a nudge. The
 * pipeline then reads every sheet itself, exactly as it always has, so
 * nothing here needs to know the shape of any form, and the same file
 * goes into each of the four responses spreadsheets unchanged.
 */

const GH_REPO = 'shakeyshep-source/coaching-dashboard';

function onFormSubmitNudgeGitHub() {
  const token = PropertiesService.getScriptProperties().getProperty('GH_TOKEN');
  if (!token) {
    console.error('No GH_TOKEN script property set — see AUTOMATION_SETUP.md step 6.');
    return;
  }

  const response = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GH_REPO}/dispatches`,
    {
      method: 'post',
      contentType: 'application/json',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
      },
      payload: JSON.stringify({ event_type: 'form-response' }),
      muteHttpExceptions: true,
    }
  );

  // 204 No Content is success for this endpoint. Anything else is
  // logged rather than thrown: a failed nudge must never stop the form
  // response itself being saved, and the hourly backstop schedule will
  // pick the entry up regardless.
  const code = response.getResponseCode();
  if (code !== 204) {
    console.error(`GitHub dispatch failed: ${code} ${response.getContentText()}`);
  }
}
