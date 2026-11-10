// Progressive enhancement only: server tickets and service transactions remain authoritative.
document.querySelectorAll('form[data-once]').forEach(form => {
  form.addEventListener('submit', event => {
    if (form.dataset.submitting === '1') {
      event.preventDefault();
      return;
    }
    form.dataset.submitting = '1';
    form.querySelectorAll('button').forEach(button => {
      button.disabled = true;
      button.textContent = '처리 중…';
    });
  });
});
window.addEventListener('pageshow', event => {
  if (event.persisted) window.location.reload();
});
