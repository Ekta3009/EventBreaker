package test;

import com.eventbreaker.consumer.annotation.EventBreakerConsumer;

/**
 * Adversarial consumer — generic-typed dependency.
 *
 * Tests that EventBreaker correctly strips generic type parameters when:
 *   - generating mock() calls  — Repository<Product>.class is invalid Java
 *   - generating import statements — import test.Repository<Product> is invalid
 *   - generating stub files  — stub file must be Repository.java, not Repository<Product>.java
 *
 * This is realistic: Spring Data repositories are commonly typed as
 * JpaRepository<Order, Long> or similar parameterised interfaces.
 */
public class GenericDepConsumer {

    private ProductRepository<Product> productRepository;
    private EventPublisher eventPublisher;

    @EventBreakerConsumer(eventType = "ProductViewed")
    public void handle(ProductViewed event) {
        Product product = productRepository.findById(event.getProductId());
        eventPublisher.publish("VIEW_RECORDED", event.getProductId());
    }
}
