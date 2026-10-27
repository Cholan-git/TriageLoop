"""Principal user journeys through the real UI, server and database."""
import re

import pytest
from playwright.sync_api import expect

from e2e_support import Server

pytestmark = pytest.mark.e2e


def load_demo(page):
    page.goto(page.url.split("#")[0] + "#/settings")
    page.click("#load-demo")
    page.click("#dialog [data-submit]")
    expect(page.locator("#k-flags")).to_have_text("3")


def kpi(page, name):
    return page.locator(f"#k-{name}")


def toast(page, text):
    expect(page.locator(".toast").filter(has_text=text).first).to_be_visible()


# ---- start-up & sign-in -------------------------------------------------------------------

def test_clean_install_shows_empty_state(make_page):
    page = make_page()
    expect(kpi(page, "waiting")).to_have_text("0")
    expect(kpi(page, "wait")).to_have_text("—")
    expect(page.locator("#dash-queue")).to_contain_text("No patients in the department")
    assert page.errors == []


def test_wrong_password_shows_error(make_page, server):
    page = make_page(login=False)
    page.goto(server.url + "/")
    expect(page).to_have_url(re.compile(r"/login$"))
    page.fill("input[name=username]", "admin")
    page.fill("input[name=password]", "nope-nope-nope")
    page.click("button[type=submit]")
    expect(page.locator(".form-error")).to_have_text("Incorrect username or password.")


# ---- core clinical journey ------------------------------------------------------------------

def test_demo_review_keeps_urgent_explanation(make_page):
    page = make_page()
    load_demo(page)
    expect(kpi(page, "waiting")).to_have_text("10")
    expect(page.locator("#why-title")).to_have_text("Why is P11 flagged?")
    expect(page.locator("#why")).to_contain_text("R-NEWS2-HIGH")
    page.click("#dash-queue [data-action=review][data-pid=P11]")
    page.fill("#dialog textarea[name=note]", "Escalated to senior clinician")
    page.click("#dialog [data-submit]")
    toast(page, "Review recorded for P11")
    # Still urgent (NEWS2 7), still explained, rise and overdue flags cleared by the review.
    row = page.locator("#dash-queue .prow").filter(has_text="P11")
    expect(row).to_contain_text("URGENT REVIEW")
    expect(row).to_contain_text("NEWS2 7 (≥ 7)")
    expect(row).not_to_contain_text("NEWS2 rose")
    expect(row.locator(".w")).to_have_text("1 h 5 min")      # time in department is not reset
    page.goto(page.url.split("#")[0] + "#/patients/P11")
    expect(page.locator(".stat").filter(has_text="Reviews")).to_contain_text("1")
    expect(page.locator(".timeline")).to_contain_text("Escalated to senior clinician")
    assert page.errors == []


def test_simulation_controls_and_repeated_clicks(make_page):
    page = make_page()
    load_demo(page)
    expect(page.locator("#clock-time")).to_have_text("10:42")
    page.click("[data-sim=ct_down]")
    toast(page, "outage unchanged")                        # 118 min left beats 60 min
    adv = page.locator("[data-sim=advance]")
    adv.dblclick()                                         # second click ignored while busy
    expect(page.locator("#clock-time")).to_have_text("10:52")
    page.wait_for_timeout(500)
    expect(page.locator("#clock-time")).to_have_text("10:52")
    page.click("[data-sim=arrivals]")
    toast(page, "Added 5 synthetic arrivals")
    expect(kpi(page, "waiting")).to_have_text("15")
    page.click("[data-sim=scenario]")
    expect(page.locator("#scenario-label")).to_have_text("Scenario: Surge")
    expect(page.locator("#arrivals-label")).to_have_text("Add 10 arrivals")
    expect(kpi(page, "waiting")).to_have_text("15")           # switching scenario wipes nothing


def test_register_arrival_validation_and_queue(make_page):
    page = make_page()
    page.goto(page.url.split("#")[0] + "#/queue")
    page.click("text=Register arrival")
    page.click("#dialog [data-submit]")
    expect(page.locator("#dialog [data-error]")).to_have_text("Choose an ESI level.")
    page.select_option("#dialog select[name=esi]", "2")
    page.fill("#dialog input[name=complaint]", "Crushing chest pain")
    page.check("#dialog input[name=needs][value=CT]")
    page.fill("#dialog input[name=rr]", "26")
    page.fill("#dialog input[name=spo2]", "91")
    page.fill("#dialog input[name=hr]", "95")
    page.click("#dialog [data-submit]")
    toast(page, "P01 added to the queue")
    expect(page.locator("#q-list")).to_contain_text("Crushing chest pain")
    page.fill("#q-search", "zzz")
    expect(page.locator("#q-list")).to_contain_text("No patients match")
    page.fill("#q-search", "crushing")
    row = page.locator("#q-list .prow").first
    expect(row).to_contain_text("URGENT REVIEW")            # NEWS2 7 from the entered vitals
    expect(page.locator("#nav-flags")).to_have_text("1")


def test_vitals_and_lifecycle(make_page):
    page = make_page()
    load_demo(page)
    page.goto(page.url.split("#")[0] + "#/patients/P02")
    page.click("[data-action=vitals]")
    page.fill("#dialog input[name=rr]", "30")
    page.click("#dialog [data-submit]")
    toast(page, "Vitals recorded for P02")
    expect(page.locator(".data tbody tr").first).to_contain_text("30")
    page.click("[data-action=transition][data-to=in_treatment]")
    page.click("#dialog [data-submit]")
    toast(page, "P02 moved to in treatment")
    page.click("[data-action=transition][data-to=discharged]")
    page.click("#dialog [data-submit]")
    toast(page, "P02 moved to discharged")
    expect(page.locator("h1 .pill.closed")).to_have_text("CLOSED")
    page.goto(page.url.split("#")[0] + "#/dashboard")
    expect(kpi(page, "waiting")).to_have_text("9")


def test_resources_page_outage_and_recover(make_page):
    page = make_page()
    load_demo(page)
    page.goto(page.url.split("#")[0] + "#/resources")
    lab = page.locator("section.card", has=page.locator("#res-LAB"))
    lab.locator("[data-action=outage]").click()
    page.fill("#dialog input[name=minutes]", "45")
    page.click("#dialog [data-submit]")
    toast(page, "Lab offline for 45 min")
    expect(lab.locator(".rstat").first).to_have_text("Offline")
    lab.locator("[data-action=recover]").click()
    toast(page, "Lab back online")
    expect(lab.locator(".rstat").first).to_have_text("Free")


def test_event_log_filters(make_page):
    page = make_page()
    load_demo(page)
    page.goto(page.url.split("#")[0] + "#/events")
    expect(page.locator("#e-body tr").first).to_be_visible()
    page.fill("#e-patient", "P11")
    refs = page.locator("#e-body tr td:nth-child(5)")
    expect(refs.filter(has_not_text="P11")).to_have_count(0)
    expect(page.locator("#e-body")).to_contain_text("New patient arrival")
    page.fill("#e-patient", "")
    page.select_option("#e-cat", "review")
    expect(page.locator("#e-pager")).to_contain_text("of 1")
    page.check("#e-scope")
    page.select_option("#e-cat", "security")
    expect(page.locator("#e-body")).to_contain_text("Signed in")


# ---- layout & theme preferences -----------------------------------------------------------------

def test_theme_switch_and_persistence(make_page):
    page = make_page()
    html = page.locator("html")
    expect(html).to_have_attribute("data-theme", "light")
    page.click("#theme-btn")
    expect(html).to_have_attribute("data-theme", "dark")
    bg_dark = page.locator("body").evaluate("e => getComputedStyle(e).backgroundColor")
    page.reload()
    expect(html).to_have_attribute("data-theme", "dark")
    for route in ("queue", "resources", "events", "settings"):
        page.goto(page.url.split("#")[0] + f"#/{route}")
        expect(html).to_have_attribute("data-theme", "dark")
    page.check('input[name=theme][value=light]', force=True)
    expect(html).to_have_attribute("data-theme", "light")
    assert page.locator("body").evaluate("e => getComputedStyle(e).backgroundColor") != bg_dark


def test_system_theme_follows_os_unless_overridden(make_page):
    page = make_page(color_scheme="dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")   # default: light
    page.goto(page.url.split("#")[0] + "#/settings")
    page.check('input[name=theme][value=system]', force=True)
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.emulate_media(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    page.check('input[name=theme][value=dark]', force=True)
    page.emulate_media(color_scheme="light")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")    # explicit wins


def test_sidebar_and_panel_collapse_persist(make_page):
    page = make_page()
    load_demo(page)
    page.goto(page.url.split("#")[0] + "#/dashboard")
    toggle = page.locator("#sidebar-toggle")
    toggle.click()
    expect(page.locator("html")).to_have_attribute("data-sidebar", "collapsed")
    expect(toggle).to_have_attribute("aria-expanded", "false")
    expect(page.locator("nav a[data-route=dashboard]")).to_have_attribute("aria-current", "page")
    expect(page.locator("nav a[data-route=queue] .lbl")).to_be_hidden()
    page.click('[data-action=toggle-panel][data-panel=alerts]')
    expect(page.locator("#panel-alerts-body")).to_be_hidden()
    expect(page.locator('[data-badge=alerts]')).to_have_text("3 urgent")    # still discoverable
    expect(page.locator("#nav-flags")).to_have_text("3")
    page.reload()
    expect(page.locator("html")).to_have_attribute("data-sidebar", "collapsed")
    expect(page.locator("#panel-alerts-body")).to_be_hidden()
    expect(kpi(page, "flags")).to_have_text("3")                              # data unaffected
    page.click('[data-action=toggle-panel][data-panel=alerts]')
    expect(page.locator("#panel-alerts-body")).to_be_visible()


def test_mobile_drawer(make_page):
    page = make_page(viewport=(390, 844))
    expect(page.locator("#sidebar")).not_to_be_in_viewport()
    page.click("#menu-btn")
    expect(page.locator("#sidebar")).to_be_in_viewport()
    page.click("nav a[data-route=events]")
    expect(page.locator("h1")).to_contain_text("Event Log")
    expect(page.locator("#sidebar")).not_to_be_in_viewport()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def test_keyboard_navigation(make_page):
    page = make_page()
    page.bring_to_front()
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    page.keyboard.press("Enter")
    expect(page.locator("#main")).to_be_focused()
    page.focus("[data-sim=advance]")
    page.keyboard.press("Enter")
    toast(page, "Clock advanced 10 min")
    page.focus("nav a[data-route=settings]")
    page.keyboard.press("Enter")
    expect(page.locator("h1")).to_have_text("Settings")
    page.focus("#user-btn")
    page.keyboard.press("Enter")
    expect(page.locator("#user-menu")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#user-menu")).to_be_hidden()
    expect(page.locator("#user-btn")).to_be_focused()


# ---- roles, errors & persistence -------------------------------------------------------------------

def test_clinician_sees_no_admin_controls(make_page):
    page = make_page(user="nurse")
    page.goto(page.url.split("#")[0] + "#/settings")
    expect(page.locator("#load-demo")).to_have_count(0)
    expect(page.locator("#ops input[name='capacity.CT']")).to_be_disabled()
    expect(page.locator("#ops button[type=submit]")).to_have_count(0)
    expect(page.locator("text=Only administrators can change these settings.")).to_be_visible()
    page.goto(page.url.split("#")[0] + "#/events")
    expect(page.locator("text=Export CSV")).to_have_count(0)


def test_unknown_route(make_page):
    page = make_page()
    page.goto(page.url.split("#")[0] + "#/nowhere")
    expect(page.locator("#view")).to_contain_text("Page not found")


def test_failed_request_is_reported_and_nothing_changes(make_page):
    page = make_page()
    page.route("**/api/sim/advance", lambda route: route.fulfill(
        status=500, content_type="application/json",
        body='{"error":{"code":"server_error","message":"Something went wrong. The change was not saved.","details":{}}}'))
    page.click("[data-sim=advance]")
    expect(page.locator(".toast.error")).to_contain_text("The change was not saved")
    clock = page.locator("#clock-time").inner_text()
    page.unroute("**/api/sim/advance")
    page.reload()
    expect(page.locator("#clock-time")).to_have_text(clock)


def test_connection_loss_banner(make_page):
    page = make_page()
    page.route("**/api/version", lambda route: route.abort())
    expect(page.locator("#conn-banner")).to_be_visible(timeout=15000)
    page.unroute("**/api/version")
    expect(page.locator("#conn-banner")).to_be_hidden(timeout=15000)


def test_state_survives_server_restart(make_page, server, browser):
    page = make_page()
    load_demo(page)
    page.click("[data-sim=advance]")
    expect(page.locator("#clock-time")).to_have_text("10:52")
    server.stop()
    restarted = Server(server.db_path).start()
    p2 = page   # cookies are per host, so the signed session is still valid after restart
    p2.goto(restarted.url + "/")
    expect(p2.locator("#clock-time")).to_have_text("10:52")
    expect(p2.locator("#k-flags")).to_have_text("3")
    restarted.stop()
    server.start()   # let the fixture shut down cleanly
