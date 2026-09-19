package com.eventbreaker.model;

public class Order {

    private final String id;
    private final double amount;
    private OrderStatus status;

    public Order(String id, double amount) {
        this.id = id;
        this.amount = amount;
        this.status = OrderStatus.CREATED;
    }

    public String getId() {
        return id;
    }

    public double getAmount() {
        return amount;
    }

    public OrderStatus getStatus() {
        return status;
    }

    public void markPaid() {
        this.status = OrderStatus.PAID;
    }
}