/* Offline browser check for scripts/offline_auth_preview.py.
 *
 * This uses only the fake provider buttons and an isolated SQLite file. It is
 * a development check, not a login implementation.
 */
const { chromium } = require("playwright");

async function openSettings(page) {
  const marker = page.getByText("认证个人档案已启用", { exact: true });
  if (!(await marker.count()) || !(await marker.first().isVisible())) {
    await page.getByRole("button", { name: "个人设置" }).click();
  }
  await marker.first().waitFor();
}

async function saveNote(page, note) {
  stage += "-open-settings";
  await openSettings(page);
  const field = page.getByLabel("还有什么希望 CampusFlow 平时记住？");
  stage += "-fill";
  await field.fill(note);
  // Streamlit reruns after the widget commits; wait for the panel to settle
  // before clicking the freshly rendered save button.
  await page.waitForTimeout(1200);
  stage += "-click-save";
  const saveButton = page.getByRole("button", { name: "保存设置", exact: true });
  if (!(await saveButton.count())) {
    throw new Error("settings panel disappeared after editing");
  }
  await saveButton.click();
  stage += "-wait-saved";
  await page.getByText(/设置已保存/).first().waitFor();
}

async function readNote(page) {
  await openSettings(page);
  await page.waitForTimeout(800);
  return page.getByLabel("还有什么希望 CampusFlow 平时记住？").inputValue();
}

async function closeSettings(page) {
  const button = page.getByRole("button", { name: /^(关闭个人设置|×)$/ });
  if (await button.count()) await button.click();
}

async function selectIdentity(page, label) {
  await page.getByRole("button", { name: label, exact: true }).click();
  await page.waitForTimeout(1500);
}

let stage = "startup";

(async () => {
  const url = process.argv[2];
  if (!url) throw new Error("offline auth preview URL required");
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CAMPUSFLOW_BROWSER_EXECUTABLE ||
      "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  });
  try {
    const aliceContext = await browser.newContext();
    const bobContext = await browser.newContext();
    const alicePage = await aliceContext.newPage();
    const bobPage = await bobContext.newPage();
    stage = "open-pages";
    await Promise.all([
      alicePage.goto(url, { waitUntil: "domcontentloaded" }),
      bobPage.goto(url, { waitUntil: "domcontentloaded" }),
    ]);
    stage = "select-identities";
    await Promise.all([
      selectIdentity(alicePage, "模拟 Alice 已认证"),
      selectIdentity(bobPage, "模拟 Bob 已认证"),
    ]);
    stage = "save-alice";
    await saveNote(alicePage, "Alice profile");
    stage = "save-bob";
    await saveNote(bobPage, "Bob profile");

    stage = "reload-alice";
    await closeSettings(alicePage);
    await alicePage.reload({ waitUntil: "domcontentloaded" });
    await selectIdentity(alicePage, "模拟 Alice 已认证");
    stage = "read-alice";
    const aliceNote = await readNote(alicePage);
    stage = "read-bob";
    const bobNote = await readNote(bobPage);
    if (aliceNote !== "Alice profile" || bobNote !== "Bob profile") {
      process.stderr.write(`alice_note_matches=${aliceNote === "Alice profile"}\n`);
      process.stderr.write(`bob_note_matches=${bobNote === "Bob profile"}\n`);
      process.stderr.write(`bob_note_length=${bobNote.length}\n`);
      throw new Error("profile isolation check failed");
    }

    stage = "logout-alice";
    await closeSettings(alicePage);
    await alicePage.getByRole("button", { name: "退出模拟认证" }).click();
    await alicePage.getByText(/当前为临时使用|已切换为临时使用/).first().waitFor();

    stage = "spoof-check";
    const spoofContext = await browser.newContext({
      extraHTTPHeaders: { "X-CampusFlow-Auth-Subject": "bob-subject" },
    });
    const spoofPage = await spoofContext.newPage();
    await spoofPage.goto(`${url}?subject=alice-subject`, { waitUntil: "domcontentloaded" });
    await spoofPage.getByRole("button", { name: "个人设置" }).click();
    await spoofPage.getByText(/需要个人档案时，请使用页面提供的认证入口/).waitFor();
    if (await spoofPage.getByText("认证个人档案已启用", { exact: true }).count()) {
      throw new Error("browser-controlled identity was accepted");
    }
    process.stdout.write("alice_profile_restored=true\n");
    process.stdout.write("bob_profile_isolated=true\n");
    process.stdout.write("logout_scope_cleared=true\n");
    process.stdout.write("browser_identity_spoof_rejected=true\n");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  process.stderr.write(`offline_auth_browser_check=failed stage=${stage} (${error.name})\n`);
  process.stderr.write(`reason=${String(error.message || "").split("\n").slice(0, 16).join(" | ")}\n`);
  process.exit(2);
});
