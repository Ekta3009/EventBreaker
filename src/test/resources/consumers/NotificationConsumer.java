package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

public class NotificationConsumer {

    private UserStore userStore;
    private NotificationGateway notificationGateway;

    @EventBreakerConsumer(eventType = "UserRegistered")
    public void handle(UserRegistered event) {

        User user = userStore.find(event.getUserId());

        Notification notification =
                new Notification(
                        user.getEmail(),
                        "Welcome!"
                );

        notificationGateway.send(notification);
    }
}