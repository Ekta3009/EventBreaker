package com.eventbreaker.event;

public class OrderCreated {
    private final String orderId;
    private final double amount;

    public OrderCreated(String orderId, double amount) {
        this.orderId = orderId;
        this.amount = amount;
    }

    public String getOrderId() {
        return orderId;
    }

    public double getAmount() {
        return amount;
    }
}
