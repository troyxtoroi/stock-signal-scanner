self.addEventListener('push', event => {
  let data = { title: '📈 股票信號', body: '有新的買入機會！' };
  try {
    data = event.data.json();
  } catch (e) {}

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: 'https://em-content.zobj.net/source/apple/354/chart-increasing_1f4c8.png',
      badge: 'https://em-content.zobj.net/source/apple/354/chart-increasing_1f4c8.png',
      requireInteraction: true,
    })
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(clients.openWindow('https://tw.stock.yahoo.com'));
});
