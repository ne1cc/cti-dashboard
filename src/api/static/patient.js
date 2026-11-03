(function () {
  'use strict';

  var STORAGE_KEY = 'cti_trial_shortlist';

  function getShortlist() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : [];
    } catch (e) {
      return [];
    }
  }

  function saveShortlist(list) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(list));
    } catch (e) {}
    updateNavBadge();
  }

  function isShortlisted(nctId) {
    return getShortlist().some(function (item) {
      return item.nct_id === nctId;
    });
  }

  function toggleShortlist(itemData) {
    var list = getShortlist();
    var idx = -1;
    for (var i = 0; i < list.length; i++) {
      if (list[i].nct_id === itemData.nct_id) {
        idx = i;
        break;
      }
    }
    if (idx >= 0) {
      list.splice(idx, 1);
      saveShortlist(list);
      return false;
    } else {
      list.push({
        nct_id: itemData.nct_id,
        brief_title: itemData.brief_title,
        overall_status: itemData.overall_status,
        nearest_site: itemData.nearest_site || '',
        last_update_post_date: itemData.last_update_post_date || '',
        saved_at: Date.now()
      });
      saveShortlist(list);
      return true;
    }
  }

  function updateNavBadge() {
    var badge = document.getElementById('shortlist-count');
    if (!badge) return;
    var count = getShortlist().length;
    if (count > 0) {
      badge.textContent = ' (' + count + ')';
      badge.style.display = 'inline';
    } else {
      badge.textContent = '';
      badge.style.display = 'none';
    }
  }

  function syncButtons() {
    var buttons = document.querySelectorAll('.shortlist-btn');
    buttons.forEach(function (btn) {
      var nctId = btn.getAttribute('data-nct-id');
      if (!nctId) return;
      var active = isShortlisted(nctId);
      btn.setAttribute('aria-pressed', active ? 'true' : 'false');
      btn.classList.toggle('saved', active);
      btn.textContent = active ? 'Saved to shortlist' : 'Save to shortlist';
    });
  }

  function initListeners() {
    document.addEventListener('click', function (e) {
      var btn = e.target.closest('.shortlist-btn');
      if (btn) {
        var itemData = {
          nct_id: btn.getAttribute('data-nct-id') || '',
          brief_title: btn.getAttribute('data-title') || '',
          overall_status: btn.getAttribute('data-status') || '',
          nearest_site: btn.getAttribute('data-site') || '',
          last_update_post_date: btn.getAttribute('data-updated') || ''
        };
        if (!itemData.nct_id) return;
        var saved = toggleShortlist(itemData);
        btn.setAttribute('aria-pressed', saved ? 'true' : 'false');
        btn.classList.toggle('saved', saved);
        btn.textContent = saved ? 'Saved to shortlist' : 'Save to shortlist';
      }

      var rmBtn = e.target.closest('.remove-shortlist-btn');
      if (rmBtn) {
        var targetId = rmBtn.getAttribute('data-nct-id');
        if (targetId) {
          var updated = getShortlist().filter(function (item) {
            return item.nct_id !== targetId;
          });
          saveShortlist(updated);
          renderShortlist();
        }
      }

      var clearBtn = e.target.closest('#clear-shortlist-btn');
      if (clearBtn) {
        if (window.confirm('Are you sure you want to clear all trials from your shortlist?')) {
          saveShortlist([]);
          renderShortlist();
        }
      }

      var printBtn = e.target.closest('#print-shortlist-btn');
      if (printBtn) {
        window.print();
      }
    });
  }

  function renderShortlist() {
    var container = document.getElementById('shortlist-container');
    var actions = document.getElementById('shortlist-actions');
    if (!container) return;

    var list = getShortlist();
    container.innerHTML = '';

    if (list.length === 0) {
      if (actions) actions.style.display = 'none';
      var emptyMsg = document.createElement('p');
      emptyMsg.className = 'empty-msg';
      emptyMsg.textContent = 'Your shortlist is empty. Search for clinical trials and click "Save to shortlist" to add trials here.';
      container.appendChild(emptyMsg);
      return;
    }

    if (actions) actions.style.display = 'flex';

    var countMsg = document.createElement('p');
    countMsg.className = 'shortlist-count-msg';
    countMsg.textContent = list.length + ' trial' + (list.length === 1 ? '' : 's') + ' saved in your browser';
    container.appendChild(countMsg);

    var ul = document.createElement('ul');
    ul.className = 'results shortlist-results';

    list.forEach(function (item) {
      var li = document.createElement('li');
      li.className = 'shortlist-item';

      var article = document.createElement('article');

      var h2 = document.createElement('h2');
      var link = document.createElement('a');
      link.href = '/trials/' + encodeURIComponent(item.nct_id);
      link.textContent = item.brief_title || item.nct_id;
      h2.appendChild(link);
      article.appendChild(h2);

      var metaP = document.createElement('p');
      metaP.className = 'trial-meta';
      var statusRaw = item.overall_status || 'Unknown';
      var statusClean = statusRaw.replace(/_/g, ' ').toLowerCase();
      var formattedStatus = statusClean.charAt(0).toUpperCase() + statusClean.slice(1);
      metaP.textContent = formattedStatus + ' \u00b7 ' + item.nct_id;
      article.appendChild(metaP);

      if (item.nearest_site) {
        var siteP = document.createElement('p');
        siteP.className = 'site-meta';
        siteP.textContent = 'Nearest site: ' + item.nearest_site;
        article.appendChild(siteP);
      }

      if (item.last_update_post_date) {
        var dateP = document.createElement('p');
        dateP.className = 'date-meta';
        dateP.textContent = 'Last updated: ' + item.last_update_post_date;
        article.appendChild(dateP);
      }

      var rmBtn = document.createElement('button');
      rmBtn.type = 'button';
      rmBtn.className = 'remove-shortlist-btn button-secondary';
      rmBtn.setAttribute('data-nct-id', item.nct_id);
      rmBtn.textContent = 'Remove from shortlist';
      article.appendChild(rmBtn);

      li.appendChild(article);
      ul.appendChild(li);
    });

    container.appendChild(ul);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () {
      updateNavBadge();
      syncButtons();
      renderShortlist();
      initListeners();
    });
  } else {
    updateNavBadge();
    syncButtons();
    renderShortlist();
    initListeners();
  }
})();
