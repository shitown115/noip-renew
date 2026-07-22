import argparse
import logging
import re
import time
from sys import stdout

import pyotp
from selenium import webdriver
from selenium.common.exceptions import (NoSuchElementException,
                                        ElementNotInteractableException,
                                        TimeoutException)
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from constants import HOST_URL, LOGIN_URL, SCREENSHOTS_PATH, USER_AGENT, OTP_LENGTH

# Set up logging
logger = logging.getLogger(__name__)

logFormatter = logging.Formatter(
    "%(name)-12s %(asctime)s %(levelname)-8s %(filename)s:%(funcName)s %(message)s"
)
consoleHandler = logging.StreamHandler(stdout)
consoleHandler.setFormatter(logFormatter)
logger.addHandler(consoleHandler)


class NoIPUpdater:
    def __init__(
        self,
        username: str,
        password: str,
        totp_secret: str,
        https_proxy: str = None,
    ):
        self.username = username
        self.password = password
        self.totp_secret = totp_secret
        self.https_proxy = https_proxy
        self.browser = self._init_browser()

    def _init_browser(self, page_load_timeout: int = 90):
        logger.debug("Initializing browser...")
        options = webdriver.ChromeOptions()
        options.binary_location = "/usr/bin/chromium-browser"  # GitHub Actions 环境
        options.add_argument("disable-features=VizDisplayCompositor")
        options.add_argument("headless")
        options.add_argument("no-sandbox")
        options.add_argument("window-size=1200x800")
        options.add_argument(f"user-agent={USER_AGENT}")
        if self.https_proxy:
            options.add_argument("proxy-server=" + self.https_proxy)
        browser = webdriver.Chrome(options=options)
        logger.debug(f"Setting page load timeout to: {page_load_timeout}")
        browser.set_page_load_timeout(page_load_timeout)
        return browser

    def _fill_credentials(self):
        logger.info("Filling username and password...")
        ele_usr = self.browser.find_element("name", "username")
        ele_pwd = self.browser.find_element("name", "password")
        try:
            ele_usr.send_keys(self.username)
            ele_pwd.send_keys(self.password)
        except (NoSuchElementException, ElementNotInteractableException) as e:
            logger.error(f"Error filling credentials: {e}")
            raise Exception(f"Failed while inserting credentials: {e}")

    def _solve_captcha(self):
        logger.info("Solving captcha...")
        try:
            if logger.level == logging.DEBUG:
                self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/captcha_screen.png")
            login_button = self.browser.find_element(By.ID, "clogs-captcha-button")
            self.browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", login_button)
            time.sleep(1)
            self.browser.execute_script("arguments[0].click();", login_button)
        except (NoSuchElementException, ElementNotInteractableException) as e:
            logger.error(f"Error clicking captcha button: {e}")
            try:
                login_button = self.browser.find_element(By.CSS_SELECTOR, "button[type='submit']")
                self.browser.execute_script("arguments[0].scrollIntoView({block: 'center'});", login_button)
                time.sleep(1)
                self.browser.execute_script("arguments[0].click();", login_button)
            except Exception as e2:
                logger.error(f"Fallback click also failed: {e2}")
                raise Exception(f"Failed while trying to solve captcha: {e}")

    def _fill_otp(self):
        logger.info("Filling OTP...")
        if logger.level == logging.DEBUG:
            self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/otp_screen.png")
        otp = pyotp.TOTP(self.totp_secret).now()
        logger.info(f"Generated OTP: {otp}")  # 仅调试

        # 等待 TOTP 输入框出现
        wait = WebDriverWait(self.browser, 30)
        try:
            # 新的 No-IP TOTP 页面使用 input 数组，每个 digit 一个 input
            # 定位所有 type="tel" 或 input 在 totp-input 容器内
            otp_inputs = wait.until(EC.presence_of_all_elements_located(
                (By.CSS_SELECTOR, "#totp-input input[type='tel'], #totp-input input")
            ))
            if len(otp_inputs) != OTP_LENGTH:
                logger.warning(f"Found {len(otp_inputs)} OTP inputs, expected {OTP_LENGTH}, trying fallback...")
                # 尝试 XPath 逐个定位
                for pos in range(OTP_LENGTH):
                    otp_elem = self.browser.find_element(
                        By.XPATH, f'//*[@id="totp-input"]/input[{pos+1}]'
                    )
                    otp_elem.send_keys(otp[pos])
                # 点击验证按钮
                verify_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@value='Verify']")))
                verify_btn.click()
                return

            # 如果成功获取全部输入框，依次填入
            for i, inp in enumerate(otp_inputs):
                inp.send_keys(otp[i])
            # 点击 Verify 按钮
            verify_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//input[@value='Verify']")))
            verify_btn.click()
        except Exception as e:
            logger.error(f"Error filling OTP: {e}")
            self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/otp_error.png")
            raise Exception(f"Failed while filling OTP: {e}")

    def login(self):
        logger.info(f"Opening {LOGIN_URL} ...")
        self.browser.get(LOGIN_URL)
        if logger.level == logging.DEBUG:
            self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/debug1.png")

        logger.info("Logging in...")
        self._fill_credentials()
        self._solve_captcha()

        # 检查是否需要 TOTP
        time.sleep(3)  # 等待跳转
        if logger.level == logging.DEBUG:
            self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/after_login.png")

        try:
            # 尝试查找 TOTP 输入框容器
            self.browser.find_element(By.ID, "totp-input")
            logger.info("TOTP input detected, filling OTP...")
            self._fill_otp()
        except NoSuchElementException:
            logger.info("No TOTP input, maybe already logged in or using email verification.")
            # 如果页面不是 TOTP，可能是因为邮箱验证，但我们已经启用2FA，应该不会发生
            # 保险起见，可在此处理邮箱验证（但暂不实现）

        if logger.level == logging.DEBUG:
            time.sleep(1)
            self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/debug2.png")

    def open_hosts_page(self):
        logger.info(f"Opening {HOST_URL} ...")
        try:
            self.browser.get(HOST_URL)
        except TimeoutException as e:
            logger.error(f"The process has timed out: {e}")
            self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/timeout.png")

    def update_hosts(self):
        self.open_hosts_page()
        time.sleep(3)

        hosts = self.get_hosts()
        for host in hosts:
            host_name = self.get_host_link(host).text
            expiration_days = self.get_host_expiration_days(host)
            logger.info(f"expiration days: {expiration_days}")
            if expiration_days < 7:
                logger.info(f"Host {host_name} is about to expire, confirming host..")
                host_button = self.get_host_button(host)
                self.update_host(host_button, host_name)
                logger.info(f"Host confirmed: {host_name}")
                self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/{host_name}-results.png")
            else:
                logger.info(f"Host {host_name} is yet not due, remaining days to expire: {expiration_days}")

    def update_host(self, host_button, host_name):
        logger.info(f"Updating {host_name}")
        host_button.click()
        time.sleep(1)
        try:
            upgrade_element = self.browser.find_element(By.XPATH, "//h2[@class='big']")
            intervention = upgrade_element.text == "Upgrade Now"
        except NoSuchElementException:
            intervention = False
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            intervention = False

        if intervention:
            raise Exception(f"Manual intervention required for host {host_name}. Upgrade text detected.")
        self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/{host_name}_success.png")

    def get_host_expiration_days(self, host):
        try:
            host_remaining_days = host.find_element(
                By.XPATH,
                ".//a[contains(@class, 'no-link-style') and contains(@class, 'popover-info')]",
            ).get_attribute("data-original-title")
        except NoSuchElementException:
            logger.warning("Could not find expiration days element. Assuming host is expired or element has changed.")
            return 0
        regex_match = re.search(r"\d+", host_remaining_days)
        if regex_match is None:
            raise Exception("Expiration days label does not match the expected pattern")
        expiration_days = int(regex_match.group(0))
        return expiration_days

    def get_host_link(self, host):
        return host.find_element(By.XPATH, ".//a[contains(@class, 'link-info') and contains(@class, 'cursor-pointer')]")

    def get_host_button(self, host):
        return host.find_element(
            By.XPATH, ".//following-sibling::td[contains(@class, 'text-right-md')]/button[contains(@class, 'btn-success')]"
        )

    def get_hosts(self) -> list:
        host_tds = self.browser.find_elements(By.XPATH, '//td[@data-title="Host"]')
        if len(host_tds) == 0:
            with open(f"{SCREENSHOTS_PATH}/page.html", "w") as f:
                f.write(self.browser.page_source)
            raise Exception("No hosts or host table rows not found")
        return host_tds

    def run(self) -> int:
        return_code = 0
        try:
            self.login()
            self.update_hosts()
        except Exception as e:
            logger.error(f"An error has ocurred while Robot was running: {e}")
            self.browser.save_screenshot(f"{SCREENSHOTS_PATH}/exception.png")
            return_code = 1
        finally:
            self.browser.quit()
        return return_code


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        prog="noip DDNS auto renewer",
        description="Renews each of the no-ip DDNS hosts that are below 7 days to expire period",
    )
    parser.add_argument("-u", "--username", required=True)
    parser.add_argument("-p", "--password", required=True)
    parser.add_argument("-s", "--totp-secret", required=True)
    parser.add_argument("-t", "--https-proxy", required=False)
    parser.add_argument("-d", "--debug", type=bool, default=False, required=False)
    args = vars(parser.parse_args())

    logger.setLevel(logging.DEBUG if args["debug"] else logging.INFO)

    NoIPUpdater(
        args["username"],
        args["password"],
        args["totp_secret"],
        args["https_proxy"],
    ).run()
