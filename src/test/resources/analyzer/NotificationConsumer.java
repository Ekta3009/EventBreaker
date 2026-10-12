package test;

import org.springframework.context.event.EventListener;

public class NotificationConsumer {

    private UserStore userStore;
    private NotificationGateway notificationGateway;

    @EventListener
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