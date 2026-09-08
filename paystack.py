import requests
import json
from flask import current_app
from datetime import datetime

class PaystackAPI:
    """Paystack Payment Gateway Integration"""
    
    def __init__(self):
        self.secret_key = current_app.config.get('PAYSTACK_SECRET_KEY')
        self.public_key = current_app.config.get('PAYSTACK_PUBLIC_KEY')
        self.base_url = 'https://api.paystack.co'
        self.headers = {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json'
        }
    
    def initialize_payment(self, email, amount, order_number, callback_url=None):
        """
        Initialize a payment transaction
        
        Args:
            email: Customer email
            amount: Amount in GHS (will be converted to pesewas)
            order_number: Order reference
            callback_url: URL to redirect after payment
        
        Returns:
            dict: Response from Paystack
        """
        if callback_url is None:
            callback_url = current_app.config.get('PAYSTACK_CALLBACK_URL')
        
        # Convert amount to pesewas (Paystack uses kobo/pesewas)
        amount_in_pesewas = int(amount * 100)
        
        payload = {
            'email': email,
            'amount': amount_in_pesewas,
            'reference': order_number,
            'callback_url': callback_url,
            'metadata': {
                'order_number': order_number,
                'custom_fields': [
                    {
                        'display_name': 'Order Number',
                        'variable_name': 'order_number',
                        'value': order_number
                    }
                ]
            }
        }
        
        try:
            response = requests.post(
                f'{self.base_url}/transaction/initialize',
                headers=self.headers,
                json=payload,
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    'status': False,
                    'message': f'Paystack API error: {response.status_code}',
                    'data': None
                }
        except requests.exceptions.RequestException as e:
            return {
                'status': False,
                'message': f'Connection error: {str(e)}',
                'data': None
            }
    
    def verify_payment(self, reference):
        """
        Verify a payment transaction
        
        Args:
            reference: Payment reference
        
        Returns:
            dict: Response from Paystack
        """
        try:
            response = requests.get(
                f'{self.base_url}/transaction/verify/{reference}',
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    'status': False,
                    'message': f'Paystack API error: {response.status_code}',
                    'data': None
                }
        except requests.exceptions.RequestException as e:
            return {
                'status': False,
                'message': f'Connection error: {str(e)}',
                'data': None
            }
    
    def get_transaction_details(self, reference):
        """
        Get detailed transaction information
        
        Args:
            reference: Payment reference
        
        Returns:
            dict: Transaction details
        """
        try:
            response = requests.get(
                f'{self.base_url}/transaction/{reference}',
                headers=self.headers,
                timeout=30
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                return {
                    'status': False,
                    'message': f'Paystack API error: {response.status_code}',
                    'data': None
                }
        except requests.exceptions.RequestException as e:
            return {
                'status': False,
                'message': f'Connection error: {str(e)}',
                'data': None
            }
    
    def create_webhook_signature(self, payload, signature_header):
        """
        Verify webhook signature
        
        Args:
            payload: Webhook payload
            signature_header: Paystack signature header
        
        Returns:
            bool: True if signature is valid
        """
        import hmac
        import hashlib
        
        secret = current_app.config.get('PAYSTACK_SECRET_KEY')
        computed_signature = hmac.new(
            secret.encode('utf-8'),
            payload,
            hashlib.sha512
        ).hexdigest()
        
        return hmac.compare_digest(computed_signature, signature_header)