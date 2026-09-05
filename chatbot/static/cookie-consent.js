// Cookie consent flyout: shows once until the visitor accepts or declines.
// Uses an actual cookie to remember the choice (fittingly).

function getCookie(name) {
  const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
  return match ? match[2] : null;
}

function setCookie(name, value, days) {
  const d = new Date();
  d.setTime(d.getTime() + days * 24 * 60 * 60 * 1000);
  document.cookie = `${name}=${value};expires=${d.toUTCString()};path=/;SameSite=Lax`;
}

function initCookieConsent() {
  if (getCookie('cookie_consent')) return;

  const banner = document.createElement('div');
  banner.id = 'cookie-consent';
  banner.innerHTML = `
    <p>We use a small number of cookies to keep you signed in and remember your preferences. See our <a href="/privacy" style="color:#9db4ff;">Privacy Policy</a> for details.</p>
    <div class="actions">
      <button class="decline" onclick="handleCookieChoice(false)">Decline</button>
      <button class="accept" onclick="handleCookieChoice(true)">Accept</button>
    </div>
  `;
  document.body.appendChild(banner);
  banner.style.display = 'block';
}

function handleCookieChoice(accepted) {
  setCookie('cookie_consent', accepted ? 'accepted' : 'declined', 180);
  const banner = document.getElementById('cookie-consent');
  if (banner) banner.remove();
}

document.addEventListener('DOMContentLoaded', initCookieConsent);
