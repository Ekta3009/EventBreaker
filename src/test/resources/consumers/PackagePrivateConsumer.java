package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Adversarial consumer — package-private (default-visibility) fields.
 *
 * Tests that EventBreaker correctly injects dependencies via reflection
 * even when the field visibility is neither public nor private.
 * This pattern appears in legacy Spring applications and hand-written
 * test fixtures where explicit visibility is omitted.
 */
public class PackagePrivateConsumer {

    UserRepository userRepository;
    EmailService emailService;

    @EventBreakerConsumer(eventType = "UserRegistered")
    public void handle(UserRegistered event) {
        User user = userRepository.findById(event.getUserId());
        emailService.sendWelcome(user.getEmail());
    }
}
