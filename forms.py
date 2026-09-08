from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, EmailField, TextAreaField, FloatField, IntegerField, SelectField
from wtforms.validators import DataRequired, Email, Length, NumberRange, ValidationError
from models import User

class LoginForm(FlaskForm):
    email = EmailField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired()])

class SignupForm(FlaskForm):
    name = StringField('Full Name', validators=[DataRequired(), Length(min=2, max=100)])
    email = EmailField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=6)])
    
    def validate_email(self, field):
        if User.query.filter_by(email=field.data).first():
            raise ValidationError('Email already registered.')

class ProductForm(FlaskForm):
    name = StringField('Product Name', validators=[DataRequired()])
    description = TextAreaField('Description')
    price = FloatField('Price', validators=[DataRequired(), NumberRange(min=0)])
    category = StringField('Category', validators=[DataRequired()])
    stock = IntegerField('Stock', validators=[DataRequired(), NumberRange(min=0)])
    image_url = StringField('Image URL')
    badge = StringField('Badge Text')
    status = SelectField('Status', choices=[('Active', 'Active'), ('Inactive', 'Inactive')])

class SettingsForm(FlaskForm):
    store_name = StringField('Store Name')
    support_email = EmailField('Support Email')
    free_shipping_threshold = FloatField('Free Shipping Threshold')
    paystack_public_key = StringField('Paystack Public Key')
    paystack_secret_key = StringField('Paystack Secret Key')
    email_app_password = StringField('Email App Password')

class ContactForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    email = EmailField('Email', validators=[DataRequired(), Email()])
    message = TextAreaField('Message', validators=[DataRequired()])