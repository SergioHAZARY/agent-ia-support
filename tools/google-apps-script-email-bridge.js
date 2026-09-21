/**
 * Google Apps Script — Bridge email vers n8n webhook
 *
 * Pour : itsupport@bazarchic.com (Google Workspace, pas de compte Microsoft)
 *
 * INSTALLATION :
 * 1. Se connecter sur https://script.google.com avec itsupport@bazarchic.com
 * 2. Créer un nouveau projet : "Email → n8n (Bazarchic)"
 * 3. Coller ce script dans Code.gs
 * 4. Configurer le déclencheur :
 *    - Édition > Déclencheurs du projet actuel
 *    - Ajouter un déclencheur :
 *      - Fonction : checkNewEmails
 *      - Source : Temporisé
 *      - Type : Minutes
 *      - Intervalle : Toutes les 5 minutes
 * 5. Autoriser les permissions (Gmail + URL Fetch)
 *
 * Le script vérifie les emails non lus toutes les 5 minutes et les envoie
 * au webhook n8n, exactement comme Power Automate pour les comptes Microsoft.
 */

var WEBHOOK_URL = 'https://dev-ai.app.n8n.cloud/webhook/agent-support-email';
var MAILBOX = 'itsupport@bazarchic.com';
var LABEL_PROCESSED = 'n8n-processed';

function checkNewEmails() {
  var label = GmailApp.getUserLabelByName(LABEL_PROCESSED);
  if (!label) {
    label = GmailApp.createLabel(LABEL_PROCESSED);
  }

  var threads = GmailApp.search('is:unread -label:' + LABEL_PROCESSED, 0, 20);

  for (var i = 0; i < threads.length; i++) {
    var messages = threads[i].getMessages();
    for (var j = 0; j < messages.length; j++) {
      var msg = messages[j];
      if (!msg.isUnread()) continue;

      var payload = {
        from: msg.getFrom(),
        fromName: msg.getFrom().replace(/<.*>/, '').trim(),
        to: MAILBOX,
        subject: msg.getSubject(),
        body: msg.getPlainBody().substring(0, 5000),
        messageId: msg.getId(),
        date: msg.getDate().toISOString()
      };

      try {
        var response = UrlFetchApp.fetch(WEBHOOK_URL, {
          method: 'post',
          contentType: 'application/json',
          payload: JSON.stringify(payload),
          muteHttpExceptions: true
        });

        var code = response.getResponseCode();
        if (code >= 200 && code < 300) {
          Logger.log('OK: ' + msg.getSubject() + ' -> ' + code);
        } else {
          Logger.log('ERREUR ' + code + ': ' + msg.getSubject());
          Logger.log(response.getContentText().substring(0, 200));
        }
      } catch (e) {
        Logger.log('EXCEPTION: ' + e.message);
      }
    }
    threads[i].addLabel(label);
  }
}

function testWebhook() {
  var response = UrlFetchApp.fetch(WEBHOOK_URL, {
    method: 'post',
    contentType: 'application/json',
    payload: JSON.stringify({
      from: 'test@bazarchic.com',
      fromName: 'Test BazarChic',
      to: MAILBOX,
      subject: 'Test Google Apps Script bridge',
      body: 'Ceci est un test du bridge email BazarChic vers n8n.',
      messageId: 'test-gas-001',
      date: new Date().toISOString()
    }),
    muteHttpExceptions: true
  });
  Logger.log('Test: ' + response.getResponseCode() + ' ' + response.getContentText());
}
