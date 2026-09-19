package com.eventbreaker.consumer;

import com.eventbreaker.event.OrderCreated;
import com.eventbreaker.event.OrderProcessed;
import com.eventbreaker.model.Order;
import com.eventbreaker.model.OrderStatus;
import com.eventbreaker.service.EventPublisher;
import com.eventbreaker.service.OrderRepository;
import com.eventbreaker.service.PaymentClient;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

class OrderConsumerTest {

    @Test
    void shouldProcessOrderCreatedEvent() {

        // Arrange
        OrderRepository repository = new OrderRepository();

        Order order = new Order("ORD-123", 500.0);
        repository.save(order);

        FakePaymentClient paymentClient = new FakePaymentClient();
        FakeEventPublisher eventPublisher = new FakeEventPublisher();

        OrderConsumer consumer = new OrderConsumer(
                repository,
                paymentClient,
                eventPublisher
        );

        OrderCreated event = new OrderCreated(
                "ORD-123",
                500.0
        );

        // Act
        consumer.consume(event);

        // Assert
        assertEquals(
                1,
                paymentClient.chargeCount
        );

        assertEquals(
                OrderStatus.PAID,
                repository.get("ORD-123").getStatus()
        );

        assertTrue(
                eventPublisher.publishedEvent instanceof OrderProcessed
        );
    }

    @Test
    void shouldDemonstrateDuplicateEventProblem() {

        // Arrange
        OrderRepository repository = new OrderRepository();

        Order order = new Order("ORD-123", 500.0);
        repository.save(order);

        FakePaymentClient paymentClient = new FakePaymentClient();
        FakeEventPublisher eventPublisher = new FakeEventPublisher();

        OrderConsumer consumer = new OrderConsumer(
                repository,
                paymentClient,
                eventPublisher
        );

        OrderCreated event = new OrderCreated(
                "ORD-123",
                500.0
        );

        // Act
        consumer.consume(event);
        consumer.consume(event); // duplicate delivery

        // Observe
        System.out.println(
                "Payment charge count: " + paymentClient.chargeCount
        );
    }

    private static class FakePaymentClient implements PaymentClient {

        private int chargeCount = 0;

        @Override
        public void charge(String orderId) {
            chargeCount++;
        }
    }

    private static class FakeEventPublisher implements EventPublisher {

        private Object publishedEvent;

        @Override
        public void publish(Object event) {
            publishedEvent = event;
        }
    }
}