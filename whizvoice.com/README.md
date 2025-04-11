# Whiz Voice Website

Landing page for Whiz Voice

## setup

This runs on an Alma Linux server.

0. clone repo

```
git clone git@github.com:ruthgrace/whizvoice.git
```

1. Link nginx config:

```
sudo semanage fcontext -a -t httpd_sys_content_t "/var/www/whizvoice/whizvoice.com(/.*)?"
sudo restorecon -Rv /var/www/whizvoice/whizvoice.com

sudo ln -fs /var/www/whizvoice/whizvoice.com/nginx/whizvoice.bootstrap /etc/nginx/conf.d/whizvoice.com.conf

# ensure nginx config context is httpd_config_t
sudo chcon -t httpd_config_t /etc/nginx/conf.d/whizvoice.com.conf
sudo semanage fcontext -a -t httpd_config_t "/etc/nginx/conf.d(/.*)?"
sudo restorecon -Rv /etc/nginx/conf.d

sudo service nginx reload
```

2. Set up HTTPS with Let's Encrypt:

```
sudo certbot certonly --force-renewal -a webroot -w /var/www/antimonopoly.club -d antimonopoly.club -w /var/www/antimonopoly.club -d www.antimonopoly.club

sudo ln -fs /var/www/antimonopoly.club/nginx/antimonopoly.club.conf /etc/nginx/conf.d/antimonopoly.club.conf

sudo service nginx reload
```
